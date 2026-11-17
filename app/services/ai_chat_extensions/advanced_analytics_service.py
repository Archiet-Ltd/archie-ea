"""
Advanced Analytics Service for AI Chat

Provides sophisticated analytics capabilities including:
- Portfolio health scoring
- Complexity analysis
- Cost optimization insights
- Technical debt quantification
- Capability maturity assessment
- Trend analysis and benchmarking
"""

import logging
import statistics
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List

from app.utils.tenant_sql import org_scope

logger = logging.getLogger(__name__)


class AnalyticsType(Enum):
    """Types of analytics available."""

    PORTFOLIO_HEALTH = "portfolio_health"
    COMPLEXITY_ANALYSIS = "complexity_analysis"
    COST_OPTIMIZATION = "cost_optimization"
    TECHNICAL_DEBT = "technical_debt"
    CAPABILITY_MATURITY = "capability_maturity"
    TREND_ANALYSIS = "trend_analysis"
    BENCHMARK = "benchmark"
    RISK_ANALYSIS = "risk_analysis"
    DEPENDENCY_ANALYSIS = "dependency_analysis"
    INVESTMENT_ANALYSIS = "investment_analysis"


class AdvancedAnalyticsService:
    """
    Provides advanced analytics capabilities for the AI Chat system.

    Enables sophisticated analysis of enterprise architecture data
    with scoring, trending, and benchmarking capabilities.
    """

    # Scoring weights for portfolio health
    HEALTH_WEIGHTS = {
        "technical_fitness": 0.25,
        "business_value": 0.20,
        "risk_score": 0.20,
        "cost_efficiency": 0.15,
        "capability_coverage": 0.10,
        "integration_health": 0.10,
    }

    # Maturity level definitions
    MATURITY_LEVELS = {
        1: {"name": "Initial", "description": "Ad-hoc, chaotic processes"},
        2: {"name": "Repeatable", "description": "Basic processes established"},
        3: {"name": "Defined", "description": "Standardized processes"},
        4: {"name": "Managed", "description": "Measured and controlled"},
        5: {"name": "Optimizing", "description": "Continuous improvement"},
    }

    # Technical debt categories
    DEBT_CATEGORIES = [
        "architecture_debt",
        "code_debt",
        "infrastructure_debt",
        "documentation_debt",
        "testing_debt",
        "security_debt",
    ]

    def __init__(self):
        """Initialize the advanced analytics service."""
        self.analytics_cache = {}
        self.benchmark_data = self._load_industry_benchmarks()

    def calculate_portfolio_health(
        self, scope: str = "all", filters: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Calculate comprehensive portfolio health score.

        Args:
            scope: Scope of analysis (all, domain, capability)
            filters: Optional filters to apply

        Returns:
            Portfolio health analysis with scores and recommendations
        """
        # Get portfolio data
        portfolio_data = self._get_portfolio_data(scope, filters)

        # Calculate individual scores
        scores = {
            "technical_fitness": self._calculate_technical_fitness(portfolio_data),
            "business_value": self._calculate_business_value(portfolio_data),
            "risk_score": self._calculate_risk_score(portfolio_data),
            "cost_efficiency": self._calculate_cost_efficiency(portfolio_data),
            "capability_coverage": self._calculate_capability_coverage(portfolio_data),
            "integration_health": self._calculate_integration_health(portfolio_data),
        }

        # Calculate weighted overall score
        overall_score = sum(scores[key] * self.HEALTH_WEIGHTS[key] for key in self.HEALTH_WEIGHTS)

        # Identify top issues
        issues = self._identify_health_issues(portfolio_data, scores)

        # Generate recommendations
        recommendations = self._generate_health_recommendations(scores, issues)

        return {
            "overall_score": round(overall_score, 2),
            "score_grade": self._score_to_grade(overall_score),
            "component_scores": scores,
            "score_weights": self.HEALTH_WEIGHTS,
            "top_issues": issues[:5],
            "recommendations": recommendations,
            "portfolio_summary": {
                "total_applications": len(portfolio_data.get("applications", [])),
                "total_capabilities": len(portfolio_data.get("capabilities", [])),
                "scope": scope,
            },
            "benchmark_comparison": self._compare_to_benchmark(overall_score),
        }

    def analyze_complexity(self, target: str, target_id: int = None) -> Dict[str, Any]:
        """
        Analyze complexity of a system, capability, or portfolio.

        Args:
            target: Type of target (application, capability, portfolio)
            target_id: ID of specific target (optional)

        Returns:
            Complexity analysis with metrics and visualization data
        """
        if target == "application":
            return self._analyze_application_complexity(target_id)
        elif target == "capability":
            return self._analyze_capability_complexity(target_id)
        else:
            return self._analyze_portfolio_complexity()

    def _analyze_application_complexity(self, app_id: int = None) -> Dict[str, Any]:
        """Analyze application-level complexity."""
        try:
            from app.extensions import db
            from sqlalchemy import text

            if not app_id:
                return self._analyze_portfolio_complexity()

            # app_id arrives as an LLM tool argument, i.e. ultimately from chat
            # text — nothing upstream proves it belongs to the caller's org.
            _org_clause, _org_params = org_scope()
            row = db.session.execute(text(
                "SELECT name, number_of_integrations, interfaces_count, dependencies_count, "
                "technology_stack, programming_languages "
                "FROM application_components WHERE id = :app_id" + _org_clause
            ), {"app_id": app_id, **_org_params}).fetchone()

            if not row:
                return {"complexity_score": 0, "complexity_grade": "Unknown", "data_status": "application_not_found"}

            integrations = row[1] or 0
            interfaces = row[2] or 0
            dependencies = row[3] or 0
            tech_stack = (row[4] or "").split(",") if row[4] else []
            languages = (row[5] or "").split(",") if row[5] else []

            integration_score = min(integrations * 5, 100)
            tech_score = min(len(tech_stack) * 15, 100)
            dep_score = min(dependencies * 8, 100)

            overall = int((integration_score * 0.4 + tech_score * 0.3 + dep_score * 0.3))
            grade = "Critical" if overall >= 80 else ("High" if overall >= 60 else ("Medium" if overall >= 30 else "Low"))

            return {
                "complexity_score": overall,
                "complexity_grade": grade,
                "dimensions": {
                    "integration_complexity": {"score": integration_score, "inbound_interfaces": dependencies, "outbound_interfaces": interfaces, "integration_patterns": []},
                    "data_complexity": {"score": 0, "data_entities": 0, "data_sources": 0, "data_transformations": 0, "data_status": "not_tracked"},
                    "technical_complexity": {"score": tech_score, "technology_stack_size": len(tech_stack), "custom_components": 0, "third_party_dependencies": len(languages)},
                    "business_complexity": {"score": 0, "business_rules": 0, "workflows": 0, "user_roles": 0, "data_status": "not_tracked"},
                },
                "complexity_drivers": [d for d in [
                    f"{integrations} integrations" if integrations > 3 else None,
                    f"{len(tech_stack)} technologies in stack" if len(tech_stack) > 3 else None,
                    f"{dependencies} dependencies" if dependencies > 2 else None,
                ] if d],
                "simplification_opportunities": [],
            }
        except Exception as e:
            logger.warning(f"Application complexity analysis failed: {e}")
            return {"complexity_score": 0, "complexity_grade": "Not assessed", "data_status": "query_failed"}

    def _analyze_capability_complexity(self, cap_id: int = None) -> Dict[str, Any]:
        """Analyze capability-level complexity."""
        return {
            "complexity_score": 0,
            "applications_involved": 0,
            "integration_complexity": 0,
            "data_complexity": 0,
            "process_complexity": 0,
            "overlap_score": 0,
            "recommendations": [],
            "data_status": "no_assessment_recorded",
        }

    def _analyze_portfolio_complexity(self) -> Dict[str, Any]:
        """Analyze portfolio-level complexity."""
        try:
            from app.extensions import db
            from sqlalchemy import text

            # Whole-table read with no key at all. Unfiltered it scored every
            # organisation's portfolio and handed the result to the assistant as
            # "your" complexity distribution.
            _org_clause, _org_params = org_scope(keyword="WHERE")
            rows = db.session.execute(text(
                "SELECT id, number_of_integrations, interfaces_count, dependencies_count "
                "FROM application_components" + _org_clause
            ), _org_params).fetchall()

            if not rows:
                return {"overall_complexity": 0, "complexity_distribution": {}, "data_status": "no_applications"}

            scores = []
            for r in rows:
                s = min(((r[1] or 0) * 5 + (r[2] or 0) * 3 + (r[3] or 0) * 8) // 3, 100)
                scores.append(s)

            low = sum(1 for s in scores if s < 30)
            med = sum(1 for s in scores if 30 <= s < 60)
            high = sum(1 for s in scores if 60 <= s < 80)
            crit = sum(1 for s in scores if s >= 80)
            total = len(scores)

            return {
                "overall_complexity": int(sum(scores) / total) if total else 0,
                "complexity_distribution": {
                    "low": {"count": low, "percentage": round(low / total * 100, 1) if total else 0},
                    "medium": {"count": med, "percentage": round(med / total * 100, 1) if total else 0},
                    "high": {"count": high, "percentage": round(high / total * 100, 1) if total else 0},
                    "critical": {"count": crit, "percentage": round(crit / total * 100, 1) if total else 0},
                },
                "complexity_hotspots": [],
                "trend": {"direction": "stable", "change": "Baseline measurement", "period": "Current"},
            }
        except Exception as e:
            logger.warning(f"Portfolio complexity analysis failed: {e}")
            return {"overall_complexity": 0, "complexity_distribution": {}, "data_status": "query_failed"}

    def analyze_cost_optimization(
        self, scope: str = "all", optimization_targets: List[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze cost optimization opportunities.

        Args:
            scope: Scope of analysis
            optimization_targets: Specific areas to focus on

        Returns:
            Cost optimization analysis with savings opportunities
        """
        targets = optimization_targets or [
            "licensing",
            "infrastructure",
            "maintenance",
            "redundancy",
            "cloud",
        ]

        optimization_opportunities = []
        total_potential_savings = 0

        for target in targets:
            opportunity = self._analyze_cost_area(target)
            optimization_opportunities.append(opportunity)
            total_potential_savings += opportunity.get("potential_savings", 0)

        try:
            from app.extensions import db
            from sqlalchemy import text

            # Same shape as the complexity read: an unkeyed SUM. Unfiltered it
            # reported every organisation's combined spend as this tenant's
            # current total cost, and 15% of it as their savings opportunity.
            _org_clause, _org_params = org_scope(keyword="WHERE")
            cost_row = db.session.execute(text(
                "SELECT SUM(COALESCE(annual_cost, 0) + COALESCE(maintenance_cost, 0) + "
                "COALESCE(infrastructure_cost, 0) + COALESCE(support_cost, 0)) "
                "FROM application_components" + _org_clause
            ), _org_params).scalar() or 0

            current_total = float(cost_row)
            potential = round(current_total * 0.15, 2)  # conservative 15% optimization target

            return {
                "current_total_cost": current_total,
                "potential_savings": potential,
                "savings_percentage": round(potential / current_total * 100, 1) if current_total > 0 else 0,
                "optimization_opportunities": optimization_opportunities,
                "quick_wins": [],
                "implementation_roadmap": [],
                "roi_analysis": {
                    "investment_required": round(potential * 0.3, 2),
                    "annual_savings": potential,
                    "payback_period_months": 4 if potential > 0 else 0,
                },
                "data_status": "from_database" if current_total > 0 else "no_cost_data",
            }
        except Exception as e:
            logger.warning(f"Cost optimization analysis failed: {e}")
            return {"current_total_cost": 0, "potential_savings": 0, "savings_percentage": 0,
                    "optimization_opportunities": [], "data_status": "query_failed"}

    def _analyze_cost_area(self, area: str) -> Dict[str, Any]:
        """Analyze a specific cost area for optimization."""
        return {
            "area": area.replace("_", " ").title(),
            "current_cost": 0,
            "potential_savings": 0,
            "opportunities": [],
            "effort": "Unknown",
            "risk": "Unknown",
            "data_status": "no_cost_data_available",
        }

    def quantify_technical_debt(
        self, scope: str = "all", debt_categories: List[str] = None
    ) -> Dict[str, Any]:
        """
        Quantify technical debt across the portfolio.

        Args:
            scope: Scope of analysis
            debt_categories: Specific debt categories to analyze

        Returns:
            Technical debt analysis with quantification
        """
        categories = debt_categories or self.DEBT_CATEGORIES

        debt_analysis = {}
        total_debt = 0

        for category in categories:
            analysis = self._analyze_debt_category(category)
            debt_analysis[category] = analysis
            total_debt += analysis.get("estimated_cost", 0)

        return {
            "total_technical_debt": total_debt,
            "debt_to_value_ratio": 0,
            "debt_by_category": debt_analysis,
            "debt_trend": {
                "current_period": total_debt,
                "previous_period": 0,
                "change": "No historical data",
                "direction": "unknown",
            },
            "critical_debt_items": self._identify_critical_debt(),
            "remediation_plan": [],
            "debt_interest": {
                "annual_cost": 0,
                "description": "No cost data available",
            },
            "data_status": "no_assessment_recorded" if total_debt == 0 else None,
        }

    def _analyze_debt_category(self, category: str) -> Dict[str, Any]:
        """Analyze a specific technical debt category."""
        return {
            "category": category,
            "estimated_cost": 0,
            "items": 0,
            "severity": "Not assessed",
            "examples": [],
            "data_status": "no_assessment_recorded",
        }

    def assess_capability_maturity(
        self, capability_id: int = None, assessment_dimensions: List[str] = None
    ) -> Dict[str, Any]:
        """
        Assess maturity of capabilities.

        Args:
            capability_id: Specific capability to assess (None for all)
            assessment_dimensions: Dimensions to evaluate

        Returns:
            Maturity assessment with scores and improvement paths
        """
        dimensions = assessment_dimensions or [
            "process_maturity",
            "technology_maturity",
            "data_maturity",
            "automation_level",
            "governance_maturity",
        ]

        if capability_id:
            return self._assess_single_capability(capability_id, dimensions)
        else:
            return self._assess_all_capabilities(dimensions)

    def _assess_single_capability(
        self, capability_id: int, dimensions: List[str]
    ) -> Dict[str, Any]:
        """Assess maturity of a single capability."""
        dimension_scores = {}
        for dim in dimensions:
            dimension_scores[dim] = {
                "current_level": 0,
                "target_level": 0,
                "gap": 0,
                "improvement_actions": [],
            }

        return {
            "capability_id": capability_id,
            "overall_maturity": 0,
            "maturity_level": self.MATURITY_LEVELS.get(0, "Not assessed"),
            "dimension_scores": dimension_scores,
            "strengths": [],
            "improvement_areas": [],
            "maturity_roadmap": [],
            "data_status": "no_assessment_recorded",
        }

    def _assess_all_capabilities(self, dimensions: List[str]) -> Dict[str, Any]:
        """Assess maturity across all capabilities."""
        return {
            "overall_maturity": 0,
            "maturity_distribution": {
                "Level 1 - Initial": 0,
                "Level 2 - Repeatable": 0,
                "Level 3 - Defined": 0,
                "Level 4 - Managed": 0,
                "Level 5 - Optimizing": 0,
            },
            "top_performers": [],
            "improvement_priorities": [],
            "benchmark_comparison": {},
            "data_status": "no_assessment_recorded",
        }

    def analyze_trends(
        self, metric: str, time_period: str = "12m", granularity: str = "monthly"
    ) -> Dict[str, Any]:
        """
        Analyze trends for a given metric.

        Args:
            metric: Metric to analyze
            time_period: Time period to analyze
            granularity: Data granularity (daily, weekly, monthly)

        Returns:
            Trend analysis with forecasts
        """
        trend_data = self._get_trend_data(metric, time_period, granularity)

        return {
            "metric": metric,
            "time_period": time_period,
            "granularity": granularity,
            "data_points": trend_data,
            "statistics": {
                "min": min(trend_data, key=lambda x: x["value"])["value"],
                "max": max(trend_data, key=lambda x: x["value"])["value"],
                "average": statistics.mean([d["value"] for d in trend_data]),
                "std_dev": statistics.stdev([d["value"] for d in trend_data])
                if len(trend_data) > 1
                else 0,
            },
            "trend_direction": self._calculate_trend_direction(trend_data),
            "forecast": self._forecast_trend(trend_data),
            "anomalies": self._detect_anomalies(trend_data),
            "insights": self._generate_trend_insights(metric, trend_data),
        }

    def _get_trend_data(
        self, metric: str, time_period: str, granularity: str
    ) -> List[Dict[str, Any]]:
        """Get historical trend data for a metric. Returns empty if no data source."""
        return [{"date": datetime.utcnow().strftime("%Y-%m"), "value": 0}]

    def perform_benchmark_analysis(
        self, benchmark_type: str, metrics: List[str] = None
    ) -> Dict[str, Any]:
        """
        Perform benchmark analysis against industry standards.

        Args:
            benchmark_type: Type of benchmark (industry, peer, internal)
            metrics: Specific metrics to benchmark

        Returns:
            Benchmark comparison with insights
        """
        default_metrics = [
            "portfolio_health",
            "technical_debt_ratio",
            "automation_level",
            "cloud_adoption",
            "security_posture",
        ]
        metrics = metrics or default_metrics

        benchmark_results = {}
        for metric in metrics:
            benchmark_results[metric] = self._benchmark_metric(metric, benchmark_type)

        return {
            "benchmark_type": benchmark_type,
            "metrics_compared": len(metrics),
            "results": benchmark_results,
            "summary": {
                "above_benchmark": sum(
                    1 for r in benchmark_results.values() if r["position"] == "above"
                ),
                "at_benchmark": sum(1 for r in benchmark_results.values() if r["position"] == "at"),
                "below_benchmark": sum(
                    1 for r in benchmark_results.values() if r["position"] == "below"
                ),
            },
            "improvement_priorities": self._identify_benchmark_gaps(benchmark_results),
            "competitive_position": self._calculate_competitive_position(benchmark_results),
        }

    def _benchmark_metric(self, metric: str, benchmark_type: str) -> Dict[str, Any]:
        """Benchmark a specific metric."""
        return {"industry": 0, "our_value": 0, "position": "at", "data_status": "no_benchmark_data"}

    def analyze_dependencies(self, analysis_type: str, entity_id: int = None) -> Dict[str, Any]:
        """
        Analyze dependencies in the architecture.

        Args:
            analysis_type: Type of dependency analysis
            entity_id: Specific entity to analyze

        Returns:
            Dependency analysis with risk assessment
        """
        try:
            from app.extensions import db
            from flask import g
            from sqlalchemy import text

            # Scope to the caller's org (raw SQL bypasses the ORM tenant filter).
            _org = getattr(g, "current_org_id", None)
            _w = " WHERE organization_id = :org" if _org is not None else ""
            _we = " WHERE e.organization_id = :org" if _org is not None else ""
            _p = {"org": _org} if _org is not None else {}

            total_rels = db.session.execute(text(f"SELECT COUNT(*) FROM archimate_relationships{_w}"), _p).scalar() or 0
            total_els = db.session.execute(text(f"SELECT COUNT(*) FROM archimate_elements{_w}"), _p).scalar() or 0

            # Find most connected elements
            most_connected = []
            if total_rels > 0:
                rows = db.session.execute(text(
                    "SELECT e.name, COUNT(*) as cnt FROM archimate_elements e "
                    "JOIN archimate_relationships r ON r.source_id = e.id OR r.target_id = e.id "
                    f"{_we} GROUP BY e.id, e.name ORDER BY cnt DESC LIMIT 5"
                ), _p).fetchall()
                most_connected = [{"name": r[0], "connections": r[1]} for r in rows]

            return {
                "analysis_type": analysis_type,
                "total_dependencies": total_rels,
                "dependency_types": {"archimate_relationships": total_rels},
                "risk_assessment": {
                    "single_points_of_failure": 0,
                    "circular_dependencies": 0,
                    "critical_path_dependencies": 0,
                },
                "dependency_graph": {"nodes": total_els, "edges": total_rels, "clusters": 0, "most_connected": most_connected},
                "recommendations": [],
            }
        except Exception as e:
            logger.warning(f"Dependency analysis failed: {e}")
            return {"analysis_type": analysis_type, "total_dependencies": 0, "data_status": "query_failed"}

    def analyze_investments(
        self, analysis_period: str = "FY2024", grouping: str = "capability"
    ) -> Dict[str, Any]:
        """
        Analyze IT investment allocation and effectiveness.

        Args:
            analysis_period: Period to analyze
            grouping: How to group investments

        Returns:
            Investment analysis with ROI metrics
        """
        return {
            "analysis_period": analysis_period,
            "total_investment": 0,
            "investment_distribution": {
                "run_the_business": {"amount": 0, "percentage": 0},
                "grow_the_business": {"amount": 0, "percentage": 0},
                "transform_the_business": {"amount": 0, "percentage": 0},
            },
            "by_capability": [],
            "effectiveness_metrics": {
                "average_roi": 0,
                "successful_initiatives": 0,
                "on_time_delivery": 0,
                "within_budget": 0,
            },
            "optimization_recommendations": [],
            "data_status": "no_investment_data",
        }

    # Private helper methods

    def _get_portfolio_data(self, scope: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Get portfolio data based on scope and filters."""
        try:
            from app.extensions import db
            from app.models.application_portfolio import ApplicationComponent
            app_count = db.session.query(ApplicationComponent).count()
            return {
                "applications": [],
                "capabilities": [],
                "integrations": 0,
                "data_flows": 0,
                "application_count": app_count,
                "data_status": "from_database" if app_count > 0 else "no_data",
            }
        except Exception:
            logger.debug("Portfolio data query failed, returning empty default")
        return {
            "applications": [],
            "capabilities": [],
            "integrations": 0,
            "data_flows": 0,
            "application_count": 0,
            "data_status": "no_data",
        }

    def _calculate_technical_fitness(self, data: Dict[str, Any]) -> float:
        """Calculate technical fitness score from ApplicationRationalizationScore."""
        try:
            from app.extensions import db
            from sqlalchemy import func
            from app.models.models import ApplicationRationalizationScore
            avg = db.session.query(
                func.avg(ApplicationRationalizationScore.technical_health_score)
            ).scalar()
            if avg is not None:
                return round(float(avg), 1)
        except Exception:
            logger.debug("Technical fitness query failed, returning 0")
        return 0.0

    def _calculate_business_value(self, data: Dict[str, Any]) -> float:
        """Calculate business value score from ApplicationRationalizationScore."""
        try:
            from app.extensions import db
            from sqlalchemy import func
            from app.models.models import ApplicationRationalizationScore
            avg = db.session.query(
                func.avg(ApplicationRationalizationScore.business_value_score)
            ).scalar()
            if avg is not None:
                return round(float(avg), 1)
        except Exception:
            logger.debug("Business value query failed, returning 0")
        return 0.0

    def _calculate_risk_score(self, data: Dict[str, Any]) -> float:
        """Calculate risk score from ApplicationRationalizationScore."""
        try:
            from app.extensions import db
            from sqlalchemy import func
            from app.models.models import ApplicationRationalizationScore
            avg = db.session.query(
                func.avg(ApplicationRationalizationScore.vendor_risk_score)
            ).scalar()
            if avg is not None:
                return round(float(avg), 1)
        except Exception:
            logger.debug("Risk score query failed, returning 0")
        return 0.0

    def _calculate_cost_efficiency(self, data: Dict[str, Any]) -> float:
        """Calculate cost efficiency score from ApplicationRationalizationScore."""
        try:
            from app.extensions import db
            from sqlalchemy import func
            from app.models.models import ApplicationRationalizationScore
            avg = db.session.query(
                func.avg(ApplicationRationalizationScore.cost_efficiency_score)
            ).scalar()
            if avg is not None:
                return round(float(avg), 1)
        except Exception:
            logger.debug("Cost efficiency query failed, returning 0")
        return 0.0

    def _calculate_capability_coverage(self, data: Dict[str, Any]) -> float:
        """Calculate capability coverage score from real mapping data."""
        try:
            from app.extensions import db
            from flask import g
            from sqlalchemy import text
            _org = getattr(g, "current_org_id", None)
            _w = " WHERE organization_id = :org" if _org is not None else ""
            _p = {"org": _org} if _org is not None else {}
            total = db.session.execute(text(f"SELECT COUNT(*) FROM business_capability{_w}"), _p).scalar() or 0
            if total == 0:
                return 0.0
            mapped = db.session.execute(text(
                f"SELECT COUNT(DISTINCT business_capability_id) FROM application_capability_mapping{_w}"
            ), _p).scalar() or 0
            return round((mapped / total) * 100, 1)
        except Exception:
            logger.debug("Capability coverage query failed, returning 0")
            return 0.0

    def _calculate_integration_health(self, data: Dict[str, Any]) -> float:
        """Calculate integration health from ArchiMate relationship density."""
        try:
            from app.extensions import db
            from flask import g
            from sqlalchemy import text
            _org = getattr(g, "current_org_id", None)
            _w = " WHERE organization_id = :org" if _org is not None else ""
            _p = {"org": _org} if _org is not None else {}
            elements = db.session.execute(text(f"SELECT COUNT(*) FROM archimate_elements{_w}"), _p).scalar() or 0
            if elements == 0:
                return 0.0
            relationships = db.session.execute(text(f"SELECT COUNT(*) FROM archimate_relationships{_w}"), _p).scalar() or 0
            ratio = relationships / elements if elements > 0 else 0
            score = min(ratio / 2.0 * 100, 100)
            return round(score, 1)
        except Exception:
            logger.debug("Integration health query failed, returning 0")
            return 0.0

    def _score_to_grade(self, score: float) -> str:
        """Convert numeric score to letter grade."""
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"

    def _identify_health_issues(
        self, data: Dict[str, Any], scores: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """Identify top health issues from component scores."""
        issues = []
        thresholds = {
            "technical_fitness": (50, "Low technical fitness — applications may have outdated tech stacks"),
            "business_value": (50, "Low business value alignment — portfolio not aligned to business goals"),
            "risk_score": (50, "Elevated vendor/security risk across the portfolio"),
            "cost_efficiency": (50, "Cost efficiency below target — review licensing and infrastructure spend"),
            "capability_coverage": (60, "Capability gaps — business capabilities without supporting applications"),
            "integration_health": (40, "Low integration maturity — ArchiMate relationships sparse"),
        }
        for metric, (threshold, desc) in thresholds.items():
            score = scores.get(metric, 0)
            if score < threshold:
                issues.append({
                    "metric": metric,
                    "score": score,
                    "threshold": threshold,
                    "severity": "Critical" if score < threshold * 0.5 else "High",
                    "description": desc,
                })
        return sorted(issues, key=lambda x: x["score"])

    def _generate_health_recommendations(
        self, scores: Dict[str, float], issues: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Generate actionable recommendations from health issues."""
        recs = []
        rec_map = {
            "capability_coverage": {"priority": "High", "action": "Map unmapped business capabilities to supporting applications"},
            "integration_health": {"priority": "High", "action": "Define ArchiMate relationships between isolated elements"},
            "technical_fitness": {"priority": "High", "action": "Review applications with low technical health scores for modernization"},
            "risk_score": {"priority": "Medium", "action": "Address vendor concentration risks and single-vendor dependencies"},
            "cost_efficiency": {"priority": "Medium", "action": "Identify duplicate/overlapping applications for rationalization"},
            "business_value": {"priority": "Medium", "action": "Align IT investments to strategic business capabilities"},
        }
        for issue in issues[:3]:
            metric = issue["metric"]
            if metric in rec_map:
                rec = dict(rec_map[metric])
                rec["impact"] = "Improves {m} from {s:.0f} toward {t} target".format(
                    m=metric.replace("_", " "), s=issue["score"], t=issue["threshold"]
                )
                recs.append(rec)
        return recs

    def _compare_to_benchmark(self, score: float) -> Dict[str, Any]:
        """Compare score to industry benchmark."""
        return {
            "industry_average": 0,
            "our_score": score,
            "difference": 0,
            "percentile": 0,
            "data_status": "no_benchmark_data",
        }

    def _load_industry_benchmarks(self) -> Dict[str, Any]:
        """Load industry benchmark data."""
        return {}

    def _identify_critical_debt(self) -> List[Dict[str, Any]]:
        """Identify critical technical debt items."""
        return []

    def _create_debt_remediation_plan(self, debt_analysis: Dict) -> List[Dict[str, Any]]:
        """Create a technical debt remediation plan."""
        return []

    def _create_maturity_roadmap(self, dimension_scores: Dict) -> List[Dict[str, Any]]:
        """Create a maturity improvement roadmap."""
        return []

    def _calculate_trend_direction(self, data_points: List[Dict]) -> str:
        """Calculate trend direction from data points."""
        if len(data_points) < 2:
            return "stable"
        first_half = statistics.mean([d["value"] for d in data_points[: len(data_points) // 2]])
        second_half = statistics.mean([d["value"] for d in data_points[len(data_points) // 2 :]])
        if second_half > first_half * 1.05:
            return "improving"
        elif second_half < first_half * 0.95:
            return "declining"
        return "stable"

    def _forecast_trend(self, data_points: List[Dict]) -> Dict[str, Any]:
        """Forecast future trend values."""
        current = data_points[-1]["value"] if data_points else 0
        return {
            "next_period": round(current * 1.02, 1),
            "confidence": 0.75,
            "range": {"low": round(current * 0.98, 1), "high": round(current * 1.06, 1)},
        }

    def _detect_anomalies(self, data_points: List[Dict]) -> List[Dict[str, Any]]:
        """Detect anomalies in trend data."""
        return []  # No anomalies detected in sample data

    def _generate_trend_insights(self, metric: str, data_points: List[Dict]) -> List[str]:
        """Generate insights from trend data."""
        return []

    def _identify_benchmark_gaps(self, results: Dict) -> List[Dict[str, Any]]:
        """Identify gaps from benchmark analysis."""
        gaps = []
        for metric, data in results.items():
            if data["position"] == "below":
                gaps.append(
                    {
                        "metric": metric,
                        "gap": data["industry"] - data["our_value"],
                        "priority": "high"
                        if abs(data["industry"] - data["our_value"]) > 10
                        else "medium",
                    }
                )
        return sorted(gaps, key=lambda x: abs(x.get("gap", 0)), reverse=True)

    def _calculate_competitive_position(self, results: Dict) -> str:
        """Calculate overall competitive position."""
        above = sum(1 for r in results.values() if r["position"] == "above")
        total = len(results)
        if above / total > 0.6:
            return "Leader"
        elif above / total > 0.4:
            return "Challenger"
        elif above / total > 0.2:
            return "Follower"
        return "Laggard"

    def _create_optimization_roadmap(
        self, opportunities: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Create implementation roadmap for cost optimization."""
        return []
