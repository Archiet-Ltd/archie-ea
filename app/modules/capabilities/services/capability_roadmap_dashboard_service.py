"""
DEPRECATED: Import from app.modules.capabilities.services instead.
-> app.modules.capabilities.services.analysis_service

Service that assembles data for the capability roadmap dashboard.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from app import db
from app.models import ApplicationCapability, BusinessCapability
from app.models.application_portfolio import ApplicationComponent
from app.models.capability_gap_analysis import (
    CapabilityGapAnalysis,
    CapabilityGapDetail,
    GapSolutionOption,
)
from app.models.compliance_models import ComplianceControl, ComplianceRequirement
from app.models.platform_models import PlatformConfiguration
from app.models.roadmap_models import RoadmapGap, RoadmapWorkPackage
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import BusinessDomain, UnifiedCapability
from app.modules.capabilities.services.capability_gap_service import CapabilityGapAnalysisService
from app.services.decorators import transactional


class CapabilityRoadmapDashboardService:
    """Builds consolidated data for roadmap planning UI."""

    @transactional
    def __init__(self):
        self.gap_service = CapabilityGapAnalysisService()

    @transactional
    def build_context(self, architecture_id: Optional[int] = None) -> Dict:
        priority = self.gap_service.get_requirement_generation_priority(
            architecture_id=architecture_id, limit=8
        )
        metrics = self._build_metrics(priority)
        chart_config, range_options = self._build_chart_config(priority)
        table_config, status_filters = self._build_table_config(priority)

        return {
            "metrics": metrics,
            "chart_config": chart_config,
            "range_options": range_options,
            "table_config": table_config,
            "status_filters": status_filters,
            "priority_capabilities": priority,
        }

    @transactional
    def _build_metrics(self, priority: List[Dict]) -> List[Dict]:
        total_capabilities = BusinessCapability.query.count()
        high_gap_capabilities = len(priority)

        avg_maturity_gap = 0
        if priority:
            avg_maturity_gap = round(mean(item["maturity_gap"] for item in priority), 1)

        avg_coverage = (
            db.session.query(func.avg(ApplicationCapability.coverage_percent)).scalar() or 0
        )
        avg_coverage = round(avg_coverage, 1)

        critical_controls = ComplianceControl.query.filter_by(priority="critical").count()
        active_platforms = PlatformConfiguration.query.filter_by(is_active=True).count()

        return [
            {
                "label": "Total Capabilities",
                "value": str(total_capabilities),
                "delta": f"{high_gap_capabilities} with open maturity gaps",
                "icon_svg": self._icon_bar_chart(),
            },
            {
                "label": "Average Maturity Gap",
                "value": f"L{avg_maturity_gap}",
                "delta": "Target uplift required across priority scope",
                "icon_svg": self._icon_activity(),
            },
            {
                "label": "Coverage Health",
                "value": f"{max(0, 100 - avg_coverage):.1f}% gap",
                "delta": "Weighted by mapped application coverage",
                "icon_svg": self._icon_shield(),
            },
            {
                "label": "Critical Controls",
                "value": str(critical_controls),
                "delta": f"{active_platforms} active platform configs tracked",
                "icon_svg": self._icon_flag(),
            },
        ]

    def _build_chart_config(self, priority: List[Dict]) -> Tuple[Dict, List[Dict]]:
        labels = [item["capability_name"] for item in priority]
        priority_scores = [round(item["priority_score"], 2) for item in priority]
        coverage_gaps = [item["coverage_gap"] for item in priority]

        chart_config = {
            "charts": {
                "overview": {
                    "type": "bar",
                    "fill": False,
                    "borderWidth": 2,
                    "defaultRange": "priority",
                    "currency": "score",
                    "ranges": {
                        "priority": {
                            "description": "Relative priority score for requirement generation",
                            "labels": labels,
                            "data": priority_scores,
                        },
                        "coverage": {
                            "description": "Application coverage gap percentage by capability",
                            "labels": labels,
                            "data": coverage_gaps,
                        },
                    },
                }
            },
            "tables": {},
        }

        range_options = [
            {"label": "Priority score", "value": "priority", "active": True},
            {"label": "Coverage gap", "value": "coverage", "active": False},
        ]

        return chart_config, range_options

    def _build_table_config(self, priority: List[Dict]) -> Tuple[Dict, List[Dict]]:
        rows = []

        for item in priority:
            capability_id = item["capability_id"]
            severity = self._severity_for_capability(capability_id)
            control_count = self._control_count(capability_id)

            rows.append(
                {
                    "id": f"cap-{capability_id}",
                    "status": severity,
                    "capability": item["capability_name"],
                    "domain": item["domain"],
                    "gap_score": round(item["priority_score"], 1),
                    "maturity_path": f"L{item['current_maturity']} → L{item['target_maturity']}",
                    "coverage": f"{item['coverage_gap']}% gap",
                    "controls": control_count,
                    "capability_id": capability_id,
                    "details_url": f"/architecture/capabilities/{capability_id}",
                }
            )

        table_config = {
            "roadmap": {
                "rows": rows,
                "columns": [
                    {"id": "select", "label": "", "hideable": False},
                    {"id": "status", "label": "Severity", "hideable": True},
                    {"id": "capability", "label": "Capability", "hideable": True, "sortable": True},
                    {"id": "domain", "label": "Domain", "hideable": True},
                    {
                        "id": "gap_score",
                        "label": "Priority score",
                        "hideable": True,
                        "sortable": True,
                    },
                    {"id": "maturity_path", "label": "Maturity path", "hideable": True},
                    {"id": "coverage", "label": "Coverage", "hideable": True},
                    {"id": "controls", "label": "Mapped controls", "hideable": True},
                    {"id": "actions", "label": "", "hideable": False},
                ],
                "filterKey": "capability",
                "sortableColumns": ["capability", "gap_score"],
                "defaultPageSize": 5,
                "rowActions": [
                    {
                        "type": "link",
                        "label": "Open capability detail",
                        "field": "details_url",
                        "disableWhenEmpty": True,
                    },
                    {"type": "clipboard", "label": "Copy capability ID", "field": "capability_id"},
                ],
            }
        }

        status_filters = [
            {"value": "critical", "label": "Critical"},
            {"value": "high", "label": "High"},
            {"value": "medium", "label": "Medium"},
            {"value": "low", "label": "Low"},
        ]

        return table_config, status_filters

    @transactional
    def _severity_for_capability(self, capability_id: int) -> str:
        capability = db.session.get(BusinessCapability, capability_id)
        if not capability:
            return "medium"
        severity = self.gap_service._calculate_maturity_severity(capability)
        return severity or "medium"

    def _control_count(self, capability_id: int) -> int:
        return (
            ComplianceRequirement.query.filter_by(applies_to_capability_id=capability_id)
            .filter(ComplianceRequirement.control_id.isnot(None))
            .count()
        )

    @staticmethod
    def _icon_bar_chart() -> str:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h - 5 w - 5 text-muted-foreground"><path d="M3 3v18h18" /><path d="M13 17V9" /><path d="M18 17V5" /><path d="M8 17v - 3" /></svg>'

    @staticmethod
    def _icon_activity() -> str:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h - 5 w - 5 text-muted-foreground"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></svg>'

    @staticmethod
    def _icon_shield() -> str:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h - 5 w - 5 text-muted-foreground"><path d="M12 22s8 - 4 8 - 10V5l - 8 - 3 - 8 3v7c0 6 8 10 8 10" /></svg>'

    @staticmethod
    def _icon_flag() -> str:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h - 5 w - 5 text-muted-foreground"><path d="M4 5a2 2 0 0 1 2 - 2h11l - 1 4 1 4H6a2 2 0 0 1 - 2 - 2z" /><line x1="5" y1="21" x2="5" y2="9" /></svg>'

    # ============================================================================
    # PRD-C01: INTERACTIVE CAPABILITY HEAT MAP
    # ============================================================================

    @transactional
    def generate_capability_heat_map(
        self,
        metric_type: str = "maturity",
        domain_id: Optional[int] = None,
        level_filter: Optional[int] = None,
    ) -> Dict:
        """Generate heat map data for capability visualization.

        Args:
            metric_type: Type of metric to display (maturity, coverage, risk, investment)
            domain_id: Optional filter by business domain
            level_filter: Optional filter by capability level (1, 2, 3)

        Returns:
            Dict with heat map configuration and data
        """
        query = UnifiedCapability.query

        if domain_id:
            query = query.filter_by(domain_id=domain_id)
        if level_filter:
            query = query.filter_by(level=level_filter)

        capabilities = query.order_by(
            UnifiedCapability.domain_id, UnifiedCapability.level, UnifiedCapability.name
        ).all()

        # Group capabilities by domain and level
        heat_map_data = defaultdict(lambda: defaultdict(list))

        for cap in capabilities:
            domain_name = cap.domain.name if cap.domain else "Uncategorized"
            level_key = f"L{cap.level}"

            # Calculate metric value based on type
            metric_value = self._calculate_heat_map_metric(cap, metric_type)
            color_intensity = self._get_color_intensity(metric_value, metric_type)

            heat_map_data[domain_name][level_key].append(
                {
                    "id": str(cap.id),
                    "name": cap.name,
                    "code": cap.code,
                    "metric_value": metric_value,
                    "color_intensity": color_intensity,
                    "color_class": self._get_heat_map_color_class(color_intensity),
                    "description": cap.description,
                    "strategic_importance": cap.strategic_importance,
                    "drill_down_url": f"/capability/{cap.id}/details",
                }
            )

        # Get domains for axis labels
        domains = BusinessDomain.query.order_by(BusinessDomain.name).all()
        domain_labels = [d.name for d in domains]

        return {
            "success": True,
            "metric_type": metric_type,
            "heat_map_data": dict(heat_map_data),
            "domain_labels": domain_labels,
            "level_labels": ["L1", "L2", "L3"],
            "color_legend": self._get_heat_map_legend(metric_type),
            "total_capabilities": len(capabilities),
            "filters": {"domain_id": domain_id, "level_filter": level_filter},
        }

    def _calculate_heat_map_metric(self, capability: UnifiedCapability, metric_type: str) -> float:
        """Calculate metric value for heat map cell."""
        if metric_type == "maturity":
            return capability.current_maturity_level or 0
        elif metric_type == "coverage":
            mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                capability_id=capability.id
            ).count()
            return min(mappings * 10, 100)
        elif metric_type == "risk":
            gaps = (
                CapabilityGapDetail.query.filter_by(capability_id=capability.id)
                .filter(CapabilityGapDetail.severity.in_(["critical", "high"]))
                .count()
            )
            return min(gaps * 20, 100)
        elif metric_type == "investment":
            work_packages = RoadmapWorkPackage.query.filter(
                RoadmapWorkPackage.capabilities.any(id=capability.id)
            ).all()
            total_cost = sum(wp.estimated_cost or 0 for wp in work_packages)
            return min(total_cost / 10000, 100)
        return 0

    def _get_color_intensity(self, value: float, metric_type: str) -> int:
        """Convert metric value to color intensity (0 - 5)."""
        if metric_type == "maturity":
            return min(int(value), 5)
        else:
            if value >= 80:
                return 5
            elif value >= 60:
                return 4
            elif value >= 40:
                return 3
            elif value >= 20:
                return 2
            elif value > 0:
                return 1
            return 0

    def _get_heat_map_color_class(self, intensity: int) -> str:
        """Get Tailwind CSS color class for intensity level.

        Uses a colorblind-safe sequential palette (gray -> sky -> amber -> orange -> rose)
        that avoids red-green discrimination per WCAG 2.1 AA criterion 1.4.1.
        """
        colors = {
            0: "bg-gray-100 text-gray-400",
            1: "bg-sky-100 text-sky-800",
            2: "bg-sky-300 text-sky-900",
            3: "bg-amber-300 text-amber-900",
            4: "bg-orange-400 text-orange-950",
            5: "bg-rose-600 text-white",
        }
        return colors.get(intensity, "bg-gray-100 text-gray-400")

    def _get_heat_map_legend(self, metric_type: str) -> List[Dict]:
        """Get legend configuration for heat map.

        All palettes use colorblind-safe sequential colors (no red-green pairs).
        """
        legends = {
            "maturity": [
                {"label": "L0 - Not Started", "color_class": "bg-gray-100"},
                {"label": "L1 - Initial", "color_class": "bg-sky-100"},
                {"label": "L2 - Managed", "color_class": "bg-sky-300"},
                {"label": "L3 - Defined", "color_class": "bg-amber-300"},
                {"label": "L4 - Quantitatively Managed", "color_class": "bg-orange-400"},
                {"label": "L5 - Optimizing", "color_class": "bg-rose-600"},
            ],
            "coverage": [
                {"label": "0% - No Coverage", "color_class": "bg-gray-100"},
                {"label": "1 - 20% - Low", "color_class": "bg-sky-100"},
                {"label": "21 - 40% - Below Average", "color_class": "bg-sky-300"},
                {"label": "41 - 60% - Average", "color_class": "bg-amber-300"},
                {"label": "61 - 80% - Good", "color_class": "bg-orange-400"},
                {"label": "81 - 100% - Excellent", "color_class": "bg-rose-600"},
            ],
            "risk": [
                {"label": "No Risk", "color_class": "bg-gray-100"},
                {"label": "Low Risk", "color_class": "bg-sky-100"},
                {"label": "Moderate Risk", "color_class": "bg-sky-300"},
                {"label": "Elevated Risk", "color_class": "bg-amber-300"},
                {"label": "High Risk", "color_class": "bg-orange-400"},
                {"label": "Critical Risk", "color_class": "bg-rose-600"},
            ],
            "investment": [
                {"label": "No Investment", "color_class": "bg-gray-100"},
                {"label": "Minimal", "color_class": "bg-sky-100"},
                {"label": "Low", "color_class": "bg-sky-300"},
                {"label": "Medium", "color_class": "bg-amber-300"},
                {"label": "High", "color_class": "bg-orange-400"},
                {"label": "Very High", "color_class": "bg-rose-600"},
            ],
        }
        return legends.get(metric_type, legends["maturity"])

    # ============================================================================
    # PRD-C02: INTERACTIVE CAPABILITY HIERARCHY TREE
    # ============================================================================

    @transactional
    def generate_capability_hierarchy_tree(
        self, domain_id: Optional[int] = None, include_metrics: bool = True
    ) -> Dict:
        """Generate hierarchical tree structure for capabilities.

        Args:
            domain_id: Optional filter by business domain
            include_metrics: Include maturity and coverage metrics

        Returns:
            Dict with tree structure and metadata
        """
        query = UnifiedCapability.query.filter_by(level=1)

        if domain_id:
            query = query.filter_by(domain_id=domain_id)

        l1_capabilities = query.order_by(UnifiedCapability.name).all()

        tree_nodes = []
        for l1_cap in l1_capabilities:
            node = self._build_tree_node(l1_cap, include_metrics)
            tree_nodes.append(node)

        return {
            "success": True,
            "tree_nodes": tree_nodes,
            "total_l1_capabilities": len(tree_nodes),
            "domain_filter": domain_id,
            "include_metrics": include_metrics,
        }

    def _build_tree_node(self, capability: UnifiedCapability, include_metrics: bool) -> Dict:
        """Recursively build tree node with children."""
        node = {
            "id": str(capability.id),
            "name": capability.name,
            "code": capability.code,
            "level": capability.level,
            "description": capability.description,
            "strategic_importance": capability.strategic_importance,
            "domain_name": capability.domain.name if capability.domain else None,
            "children": [],
        }

        if include_metrics:
            node["metrics"] = {
                "maturity": capability.current_maturity_level or 0,
                "target_maturity": capability.target_maturity_level or 0,
                "application_count": UnifiedApplicationCapabilityMapping.query.filter_by(
                    capability_id=capability.id
                ).count(),
                "gap_count": CapabilityGapDetail.query.filter_by(
                    capability_id=capability.id
                ).count(),
            }

        # Get children
        children = (
            UnifiedCapability.query.filter_by(parent_capability_id=capability.id)
            .order_by(UnifiedCapability.name)
            .all()
        )

        for child in children:
            child_node = self._build_tree_node(child, include_metrics)
            node["children"].append(child_node)

        return node

    # ============================================================================
    # PRD-C04: AUTOMATED GAP-TO-ROADMAP GENERATION
    # ============================================================================

    @transactional
    def generate_work_packages_from_gaps(
        self, gap_analysis_id: int, created_by: int, auto_approve: bool = False
    ) -> Dict:
        """Automatically generate work packages from gap analysis results.

        Args:
            gap_analysis_id: ID of the gap analysis to process
            created_by: User ID creating the work packages
            auto_approve: Automatically set status to 'approved'

        Returns:
            Dict with created work packages and summary
        """
        gap_analysis = db.session.get(CapabilityGapAnalysis, gap_analysis_id)
        if not gap_analysis:
            return {"success": False, "error": "Gap analysis not found"}

        gap_details = (
            CapabilityGapDetail.query.filter_by(gap_analysis_id=gap_analysis_id)
            .filter(CapabilityGapDetail.severity.in_(["critical", "high"]))
            .order_by(CapabilityGapDetail.priority_score.desc())
            .all()
        )

        created_packages = []
        total_estimated_cost = 0

        for gap_detail in gap_details:
            # Get solution options for this gap
            solutions = (
                GapSolutionOption.query.filter_by(gap_detail_id=gap_detail.id)
                .order_by(GapSolutionOption.priority_rank.asc())
                .first()
            )

            # Create work package
            work_package = RoadmapWorkPackage(
                name=f"Close Gap: {gap_detail.capability.name if gap_detail.capability else 'Unknown'}",
                description=gap_detail.gap_description or "Address capability gap",
                business_capability_id=gap_detail.capability_id,
                status="draft" if not auto_approve else "approved",
                priority=self._map_severity_to_priority(gap_detail.severity),
                risk_level=gap_detail.severity,
                estimated_cost=solutions.estimated_cost
                if solutions
                else gap_detail.estimated_effort_cost,
                start_date=datetime.now().date(),
                end_date=(datetime.now() + timedelta(days=90)).date(),
                duration_days=90,
                auto_generated=True,
                source_type="gap_analysis",
                generation_method="automated",
                created_by=created_by,
                created_at=datetime.now(),
            )

            if solutions:
                work_package.business_value = solutions.expected_benefits
                work_package.estimated_cost = solutions.estimated_cost

            db.session.add(work_package)
            db.session.flush()

            # Create roadmap gap link
            roadmap_gap = RoadmapGap(
                gap_type="capability",
                capability_id=gap_detail.capability_id,
                description=gap_detail.gap_description,
                priority=self._map_severity_to_priority(gap_detail.severity),
                impact_score=gap_detail.business_impact_score,
                resolution_strategy=solutions.implementation_approach
                if solutions
                else "To be determined",
                created_at=datetime.now(),
            )
            db.session.add(roadmap_gap)

            created_packages.append(
                {
                    "id": str(work_package.id),
                    "name": work_package.name,
                    "capability_id": str(gap_detail.capability_id),
                    "estimated_cost": work_package.estimated_cost,
                    "priority": work_package.priority,
                }
            )

            total_estimated_cost += work_package.estimated_cost or 0

        db.session.commit()

        return {
            "success": True,
            "work_packages_created": len(created_packages),
            "packages": created_packages,
            "total_estimated_cost": total_estimated_cost,
            "gap_analysis_id": gap_analysis_id,
            "auto_approved": auto_approve,
        }

    def _map_severity_to_priority(self, severity: str) -> str:
        """Map gap severity to work package priority."""
        mapping = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
        return mapping.get(severity, "medium")

    # ============================================================================
    # PRD-C05: CAPABILITY-APPLICATION COVERAGE DASHBOARD
    # ============================================================================

    @transactional
    def generate_coverage_dashboard(
        self, domain_id: Optional[int] = None, level_filter: Optional[int] = None
    ) -> Dict:
        """Generate capability-application coverage matrix.

        Args:
            domain_id: Optional filter by business domain
            level_filter: Optional filter by capability level

        Returns:
            Dict with coverage matrix and analysis
        """
        query = UnifiedCapability.query

        if domain_id:
            query = query.filter_by(domain_id=domain_id)
        if level_filter:
            query = query.filter_by(level=level_filter)

        capabilities = query.order_by(UnifiedCapability.name).all()

        # Get all applications
        applications = (
            ApplicationComponent.query.filter_by(is_active=True)
            .order_by(ApplicationComponent.name)
            .all()
        )

        # Build coverage matrix
        coverage_matrix = []
        white_space_capabilities = []
        redundant_capabilities = []

        for cap in capabilities:
            mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                capability_id=cap.id
            ).all()

            app_coverage = []
            for app in applications:
                mapping = next((m for m in mappings if m.application_id == app.id), None)
                app_coverage.append(
                    {
                        "application_id": str(app.id),
                        "has_coverage": mapping is not None,
                        "coverage_level": mapping.support_level if mapping else None,
                        "mapping_id": str(mapping.id) if mapping else None,
                    }
                )

            coverage_count = len([m for m in mappings if m])

            row = {
                "capability_id": str(cap.id),
                "capability_name": cap.name,
                "capability_level": cap.level,
                "domain_name": cap.domain.name if cap.domain else None,
                "coverage_count": coverage_count,
                "coverage_percentage": (coverage_count / len(applications) * 100)
                if applications
                else 0,
                "application_coverage": app_coverage,
            }

            coverage_matrix.append(row)

            # Identify white space (no coverage)
            if coverage_count == 0:
                white_space_capabilities.append(
                    {
                        "id": str(cap.id),
                        "name": cap.name,
                        "level": cap.level,
                        "strategic_importance": cap.strategic_importance,
                    }
                )

            # Identify redundancy (excessive coverage)
            if coverage_count > 3:
                redundant_capabilities.append(
                    {
                        "id": str(cap.id),
                        "name": cap.name,
                        "coverage_count": coverage_count,
                        "applications": [m.application.name for m in mappings if m.application],
                    }
                )

        return {
            "success": True,
            "coverage_matrix": coverage_matrix,
            "applications": [
                {"id": str(app.id), "name": app.name, "type": app.application_type}
                for app in applications
            ],
            "capabilities_count": len(capabilities),
            "applications_count": len(applications),
            "white_space_count": len(white_space_capabilities),
            "redundancy_count": len(redundant_capabilities),
            "white_space_capabilities": white_space_capabilities,
            "redundant_capabilities": redundant_capabilities,
            "average_coverage": sum(r["coverage_percentage"] for r in coverage_matrix)
            / len(coverage_matrix)
            if coverage_matrix
            else 0,
        }

    # ============================================================================
    # PRD-C06: GUIDED MATURITY ASSESSMENT
    # ============================================================================

    @transactional
    def get_maturity_assessment_questions(self, capability_id: int) -> Dict:
        """Get maturity assessment questionnaire for a capability.

        Args:
            capability_id: ID of capability to assess

        Returns:
            Dict with assessment questions and current state
        """
        capability = db.session.get(UnifiedCapability, capability_id)
        if not capability:
            return {"success": False, "error": "Capability not found"}

        # Define maturity assessment questions based on CMMI framework
        questions = [
            {
                "id": "process_definition",
                "category": "Process",
                "question": "Are processes for this capability formally defined and documented?",
                "weight": 20,
                "options": [
                    {"value": 0, "label": "No processes defined"},
                    {"value": 1, "label": "Ad-hoc processes"},
                    {"value": 2, "label": "Some documentation exists"},
                    {"value": 3, "label": "Processes documented"},
                    {"value": 4, "label": "Processes standardized"},
                    {"value": 5, "label": "Processes optimized"},
                ],
            },
            {
                "id": "measurement",
                "category": "Measurement",
                "question": "Are performance metrics defined and tracked for this capability?",
                "weight": 15,
                "options": [
                    {"value": 0, "label": "No metrics"},
                    {"value": 1, "label": "Basic tracking"},
                    {"value": 2, "label": "Some KPIs defined"},
                    {"value": 3, "label": "Comprehensive metrics"},
                    {"value": 4, "label": "Quantitative management"},
                    {"value": 5, "label": "Predictive analytics"},
                ],
            },
            {
                "id": "automation",
                "category": "Technology",
                "question": "What level of automation exists for this capability?",
                "weight": 15,
                "options": [
                    {"value": 0, "label": "Fully manual"},
                    {"value": 1, "label": "Minimal automation"},
                    {"value": 2, "label": "Partially automated"},
                    {"value": 3, "label": "Mostly automated"},
                    {"value": 4, "label": "Fully automated"},
                    {"value": 5, "label": "AI-driven optimization"},
                ],
            },
            {
                "id": "governance",
                "category": "Governance",
                "question": "How mature is the governance structure for this capability?",
                "weight": 20,
                "options": [
                    {"value": 0, "label": "No governance"},
                    {"value": 1, "label": "Informal oversight"},
                    {"value": 2, "label": "Basic controls"},
                    {"value": 3, "label": "Defined governance"},
                    {"value": 4, "label": "Integrated governance"},
                    {"value": 5, "label": "Continuous improvement"},
                ],
            },
            {
                "id": "skills",
                "category": "People",
                "question": "What is the skill level and training maturity for this capability?",
                "weight": 15,
                "options": [
                    {"value": 0, "label": "No trained staff"},
                    {"value": 1, "label": "Basic knowledge"},
                    {"value": 2, "label": "Some expertise"},
                    {"value": 3, "label": "Skilled team"},
                    {"value": 4, "label": "Expert team"},
                    {"value": 5, "label": "Industry-leading expertise"},
                ],
            },
            {
                "id": "integration",
                "category": "Integration",
                "question": "How well is this capability integrated with other capabilities?",
                "weight": 15,
                "options": [
                    {"value": 0, "label": "Siloed"},
                    {"value": 1, "label": "Minimal integration"},
                    {"value": 2, "label": "Some connections"},
                    {"value": 3, "label": "Well integrated"},
                    {"value": 4, "label": "Seamless integration"},
                    {"value": 5, "label": "Ecosystem orchestration"},
                ],
            },
        ]

        return {
            "success": True,
            "capability": {
                "id": str(capability.id),
                "name": capability.name,
                # D-7: L0 does not exist on a 1-5 scale -- an unassessed
                # capability must render as unassessed (None -> "-"), not a
                # fabricated level 0.
                "current_maturity": capability.current_maturity_level,
                "target_maturity": capability.target_maturity_level,
            },
            "questions": questions,
            "total_weight": sum(q["weight"] for q in questions),
        }

    @transactional
    def calculate_maturity_score(
        self,
        capability_id: int,
        responses: Dict[str, int],
        evidence_files: Optional[List[str]] = None,
        assessor_id: Optional[int] = None,
    ) -> Dict:
        """Calculate maturity score from assessment responses.

        Args:
            capability_id: ID of capability assessed
            responses: Dict mapping question IDs to selected values (0 - 5)
            evidence_files: Optional list of evidence file paths
            assessor_id: Optional ID of user conducting assessment

        Returns:
            Dict with calculated maturity level and recommendations
        """
        capability = db.session.get(UnifiedCapability, capability_id)
        if not capability:
            return {"success": False, "error": "Capability not found"}

        # Get questions to calculate weighted score
        questions_data = self.get_maturity_assessment_questions(capability_id)
        questions = questions_data["questions"]

        total_score = 0
        total_weight = 0
        category_scores = defaultdict(list)

        for question in questions:
            question_id = question["id"]
            if question_id in responses:
                value = responses[question_id]
                weight = question["weight"]
                weighted_score = value * weight
                total_score += weighted_score
                total_weight += weight * 5
                category_scores[question["category"]].append(value)

        # Calculate maturity level (0 - 5)
        maturity_percentage = (total_score / total_weight * 100) if total_weight > 0 else 0
        maturity_level = int(maturity_percentage / 20)

        # Route through the single write path when a corresponding
        # BusinessCapability source exists, so the write leaves a history
        # row and the projection listener keeps UnifiedCapability in sync.
        from app.modules.capabilities.services.capability_maturity_service import (
            record_capability_maturity,
        )

        bc_id = None
        bc_org_id = None
        if capability.source_table == "business_capability" and capability.source_id:
            try:
                bc_id = int(capability.source_id)
                bc_org_id = capability.organization_id or capability.source_org_id
            except (ValueError, TypeError):
                pass

        if bc_id is not None and bc_org_id is not None:
            record_capability_maturity(
                capability_id=bc_id,
                organization_id=bc_org_id,
                current=maturity_level,
                assessor_user_id=assessor_id,
                assessor_role="roadmap assessment",
                commit=True,
            )
            db.session.refresh(capability)
        else:
            # Fallback: write directly on UnifiedCapability when no
            # BusinessCapability source exists.
            capability.current_maturity_level = maturity_level
            capability.last_assessment_date = datetime.now()
            if assessor_id:
                capability.last_assessed_by = assessor_id
            db.session.commit()

        # Generate recommendations
        recommendations = self._generate_maturity_recommendations(
            capability, maturity_level, category_scores
        )

        return {
            "success": True,
            "capability_id": str(capability_id),
            "maturity_level": maturity_level,
            "maturity_percentage": round(maturity_percentage, 1),
            "previous_maturity": capability.current_maturity_level,
            "target_maturity": capability.target_maturity_level or 0,
            "gap_to_target": (capability.target_maturity_level or 0) - maturity_level,
            "category_scores": {
                cat: round(sum(scores) / len(scores), 1) for cat, scores in category_scores.items()
            },
            "recommendations": recommendations,
            "evidence_files": evidence_files or [],
            "assessment_date": datetime.now().isoformat(),
        }

    def _generate_maturity_recommendations(
        self, capability: UnifiedCapability, maturity_level: int, category_scores: Dict
    ) -> List[Dict]:
        """Generate improvement recommendations based on assessment."""
        recommendations = []

        # Find weakest categories
        weak_categories = sorted(category_scores.items(), key=lambda x: sum(x[1]) / len(x[1]))[:2]

        for category, scores in weak_categories:
            avg_score = sum(scores) / len(scores)
            if avg_score < 3:
                recommendations.append(
                    {
                        "category": category,
                        "priority": "high" if avg_score < 2 else "medium",
                        "current_score": round(avg_score, 1),
                        "recommendation": self._get_category_recommendation(category, avg_score),
                    }
                )

        # Add target maturity recommendation
        if capability.target_maturity_level and maturity_level < capability.target_maturity_level:
            gap = capability.target_maturity_level - maturity_level
            recommendations.append(
                {
                    "category": "Overall",
                    "priority": "critical" if gap > 2 else "high",
                    "current_score": maturity_level,
                    "recommendation": f"Focus on closing {gap}-level maturity gap to reach target L{capability.target_maturity_level}",
                }
            )

        return recommendations

    def _get_category_recommendation(self, category: str, score: float) -> str:
        """Get specific recommendation for category."""
        recommendations = {
            "Process": "Formalize and document processes. Create standard operating procedures.",
            "Measurement": "Define KPIs and implement tracking mechanisms. Establish baseline metrics.",
            "Technology": "Invest in automation tools. Evaluate technology solutions to reduce manual effort.",
            "Governance": "Establish governance framework. Define roles, responsibilities, and decision rights.",
            "People": "Develop training programs. Hire or upskill team members.",
            "Integration": "Map integration points. Implement APIs and data sharing mechanisms.",
        }
        return recommendations.get(category, "Assess and improve this area")

    # ============================================================================
    # PRD-C07: VALUE STREAM MAPPING VISUALIZATION
    # ============================================================================

    @transactional
    def generate_value_stream_map(
        self, domain_id: Optional[int] = None, process_filter: Optional[str] = None
    ) -> Dict:
        """Generate value stream mapping data with capability overlay.

        Args:
            domain_id: Optional filter by business domain
            process_filter: Optional filter by process name

        Returns:
            Dict with value stream stages and capability mappings
        """
        # Define generic value stream stages
        value_stream_stages = [
            {"id": "discover", "name": "Discover", "description": "Identify customer needs"},
            {"id": "design", "name": "Design", "description": "Design solution"},
            {"id": "develop", "name": "Develop", "description": "Build and test"},
            {"id": "deliver", "name": "Deliver", "description": "Deploy to production"},
            {"id": "support", "name": "Support", "description": "Maintain and support"},
        ]

        # Get capabilities for each stage
        query = UnifiedCapability.query
        if domain_id:
            query = query.filter_by(domain_id=domain_id)

        capabilities = query.all()

        # Map capabilities to stages based on keywords
        stage_mappings = defaultdict(list)

        for cap in capabilities:
            stage = self._map_capability_to_stage(cap)

            # Get work packages for this capability
            work_packages = RoadmapWorkPackage.query.filter(
                RoadmapWorkPackage.capabilities.any(id=cap.id)
            ).all()

            stage_mappings[stage].append(
                {
                    "capability_id": str(cap.id),
                    "capability_name": cap.name,
                    "capability_level": cap.level,
                    "maturity": cap.current_maturity_level or 0,
                    "strategic_importance": cap.strategic_importance,
                    "work_package_count": len(work_packages),
                    "total_investment": sum(wp.estimated_cost or 0 for wp in work_packages),
                }
            )

        # Calculate stage metrics
        for stage_info in value_stream_stages:
            stage_id = stage_info["id"]
            stage_caps = stage_mappings[stage_id]

            stage_info["capability_count"] = len(stage_caps)
            stage_info["avg_maturity"] = (
                (sum(c["maturity"] for c in stage_caps) / len(stage_caps)) if stage_caps else 0
            )
            stage_info["total_investment"] = sum(c["total_investment"] for c in stage_caps)
            stage_info["capabilities"] = stage_caps

        return {
            "success": True,
            "value_stream_stages": value_stream_stages,
            "total_capabilities_mapped": sum(
                len(stage_mappings[s["id"]]) for s in value_stream_stages
            ),
            "domain_filter": domain_id,
            "process_filter": process_filter,
        }

    def _map_capability_to_stage(self, capability: UnifiedCapability) -> str:
        """Map capability to value stream stage based on keywords."""
        name_lower = capability.name.lower()
        desc_lower = (capability.description or "").lower()

        if any(
            kw in name_lower or kw in desc_lower
            for kw in ["research", "analysis", "planning", "strategy"]
        ):
            return "discover"
        elif any(
            kw in name_lower or kw in desc_lower for kw in ["design", "architecture", "blueprint"]
        ):
            return "design"
        elif any(
            kw in name_lower or kw in desc_lower
            for kw in ["development", "build", "implementation", "coding"]
        ):
            return "develop"
        elif any(
            kw in name_lower or kw in desc_lower
            for kw in ["deployment", "release", "delivery", "launch"]
        ):
            return "deliver"
        elif any(
            kw in name_lower or kw in desc_lower
            for kw in ["support", "maintenance", "operations", "monitoring"]
        ):
            return "support"
        else:
            return "discover"
