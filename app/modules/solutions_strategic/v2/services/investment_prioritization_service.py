"""
Investment Prioritization Service

Analyzes and prioritizes investments in business capabilities based on:
- Strategic importance and business value
- Current coverage and maturity gaps
- Risk assessment and single points of failure
- ROI and cost-benefit analysis
- Technology debt and modernization needs
"""

import logging
from datetime import datetime
from typing import Dict, List

from app import db
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability
from app.services.application_cost_accessor import get_annual_cost_float
from .decorators import transactional

# Timeframe string → integer months for ROI calculator
_TIMEFRAME_MONTHS: Dict[str, int] = {
    "12 - 18 months": 15,
    "6 - 12 months": 9,
    "3 - 6 months": 4,
}

logger = logging.getLogger(__name__)


class InvestmentPrioritizationService:
    """
    Service for prioritizing investments in business capabilities.

    Provides data-driven investment recommendations based on multiple factors:
    - Strategic alignment and business criticality
    - Current capability coverage and maturity
    - Risk factors and dependencies
    - Technology health and modernization needs
    """

    def __init__(self):
        pass

    @transactional
    def analyze_investment_priorities(self, include_risk_analysis: bool = True) -> Dict:
        """
        Comprehensive investment prioritization analysis.

        Args:
            include_risk_analysis: Include risk assessment in prioritization

        Returns:
            Dict with prioritized investment recommendations
        """
        from app.middleware.tenant_context import current_org_id
        from app.models.application_portfolio import ApplicationComponent

        # Get all unified capabilities
        capabilities = UnifiedCapability.query.all()

        # Batch-load all capability mappings scoped to this organization, and
        # the application each one points at, so a capability's recorded cost
        # is the sum of its mapped applications' recorded annual cost.
        # UnifiedApplicationCapabilityMapping has no organization_id, so scope through
        # ApplicationComponent (TenantMixin, automatically filtered by current_org_id)
        mappings_by_cap = {}
        apps_by_cap = {}
        try:
            org_id = current_org_id()
            if org_id is not None:
                rows = (
                    db.session.query(UnifiedApplicationCapabilityMapping, ApplicationComponent)
                    .join(
                        ApplicationComponent,
                        UnifiedApplicationCapabilityMapping.application_component_id == ApplicationComponent.id
                    )
                    .all()
                )
                for mapping, component in rows:
                    mappings_by_cap.setdefault(mapping.unified_capability_id, []).append(mapping)
                    apps_by_cap.setdefault(mapping.unified_capability_id, []).append(component)
        except Exception:
            logger.debug("Could not batch-load capability mappings", exc_info=True)

        # Build capability → initiative map via RoadmapItem.linked_capabilities JSON
        linked_initiatives = self._get_linked_initiatives()

        # Analyze each capability for investment priority
        capability_scores = []
        for capability in capabilities:
            score_data = self._calculate_capability_score(
                capability, include_risk_analysis, mappings_by_cap, linked_initiatives, apps_by_cap
            )
            capability_scores.append(score_data)

        # Sort by investment priority (highest first)
        capability_scores.sort(key=lambda x: x["investment_priority_score"], reverse=True)

        # Categorize by priority levels
        critical_investments = [c for c in capability_scores if c["priority_level"] == "CRITICAL"]
        high_investments = [c for c in capability_scores if c["priority_level"] == "HIGH"]
        medium_investments = [c for c in capability_scores if c["priority_level"] == "MEDIUM"]
        low_investments = [c for c in capability_scores if c["priority_level"] == "LOW"]

        # Calculate portfolio metrics
        portfolio_metrics = self._calculate_portfolio_metrics(capability_scores, apps_by_cap)

        # Generate investment recommendations
        recommendations = self._generate_investment_recommendations(capability_scores)

        return {
            "total_capabilities": len(capabilities),
            "capability_scores": capability_scores,
            "critical_investments": critical_investments,
            "high_investments": high_investments,
            "medium_investments": medium_investments,
            "low_investments": low_investments,
            "portfolio_metrics": portfolio_metrics,
            "recommendations": recommendations,
            "analysis_date": datetime.utcnow().isoformat(),
        }

    def _get_linked_initiatives(self) -> Dict[int, Dict]:
        """
        Build a map of capability_id → initiative budget data.

        Traverses RoadmapItem.linked_capabilities JSON to resolve the
        StrategicInitiative that funds each BusinessCapability.
        """
        cap_to_initiative: Dict[int, Dict] = {}
        try:
            from app.models.strategic import RoadmapItem, StrategicInitiative

            items = (
                RoadmapItem.query
                .filter(
                    RoadmapItem.initiative_id.isnot(None),
                    RoadmapItem.linked_capabilities.isnot(None),
                )
                .all()
            )
            if not items:
                return cap_to_initiative

            # Batch-load linked initiatives to avoid N+1
            initiative_ids = {item.initiative_id for item in items}
            initiatives = {
                ini.id: ini
                for ini in StrategicInitiative.query.filter(
                    StrategicInitiative.id.in_(initiative_ids)
                ).all()
            }

            for item in items:
                initiative = initiatives.get(item.initiative_id)
                if not initiative:
                    continue
                for raw_id in item.get_linked_capabilities():
                    try:
                        cap_id = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    existing = cap_to_initiative.get(cap_id)
                    new_budget = initiative.budget_allocated or 0.0
                    # When multiple items link the same capability, keep the highest-budget initiative
                    if existing is None or new_budget > (existing.get("budget_allocated") or 0.0):
                        cap_to_initiative[cap_id] = {
                            "initiative_id": initiative.id,
                            "initiative_name": initiative.name,
                            "budget_allocated": new_budget,
                            "budget_spent": initiative.budget_spent or 0.0,
                            "budget_remaining": initiative.budget_remaining,
                            "business_value_score": initiative.business_value_score,
                            "risk_level": initiative.risk_level,
                            "status": initiative.status,
                        }
        except Exception:
            logger.debug("Could not build linked-initiatives map", exc_info=True)
        return cap_to_initiative

    def _calculate_capability_score(
        self, capability, include_risk: bool,
        mappings_by_cap: Dict = None,
        linked_initiatives: Dict = None,
        apps_by_cap: Dict = None,
    ) -> Dict:
        """Calculate investment priority score for a single capability."""

        # Use pre-loaded mappings dict (batch) instead of per-capability query
        app_mappings = (mappings_by_cap or {}).get(capability.id, [])
        mapped_apps = (apps_by_cap or {}).get(capability.id, [])

        coverage_count = len(app_mappings)

        # Strategic importance score (0 - 25 points)
        strategic_score = self._calculate_strategic_score(capability)

        # Coverage gap score (0 - 25 points)
        coverage_score = self._calculate_coverage_score(coverage_count, capability)

        # Maturity gap score (0 - 25 points)
        maturity_score = self._calculate_maturity_score(capability)

        # Risk score (0 - 25 points)
        risk_score = 0
        if include_risk:
            risk_score = self._calculate_risk_score(capability, app_mappings)

        # Total investment priority score (0 - 100)
        total_score = strategic_score + coverage_score + maturity_score + risk_score

        # Determine priority level
        if total_score >= 80:
            priority_level = "CRITICAL"
        elif total_score >= 60:
            priority_level = "HIGH"
        elif total_score >= 40:
            priority_level = "MEDIUM"
        else:
            priority_level = "LOW"

        # Resolve linked initiative for this capability (if any)
        linked_initiative = (linked_initiatives or {}).get(capability.id)

        # Recorded annual cost of this capability's mapped applications,
        # read through the one canonical cost accessor. None when nothing is
        # recorded for any of them.
        recorded_cost = self._recorded_cost_for_apps(mapped_apps)

        # Calculate estimated investment need — real recorded cost, or the
        # linked initiative's remaining budget; never an invented figure.
        investment_need = self._estimate_investment_need(
            capability, total_score, app_mappings, linked_initiative, recorded_cost
        )

        has_real_budget = investment_need.get("has_real_budget", False)

        return {
            "capability_id": capability.id,
            "capability_name": capability.name,
            "capability_domain": getattr(capability, 'industry_domain', None) or getattr(capability, 'business_domain', None) or "Unknown",
            "strategic_importance": capability.strategic_importance,
            "coverage_count": coverage_count,
            "strategic_score": strategic_score,
            "coverage_score": coverage_score,
            "maturity_score": maturity_score,
            "maturity_assessed": (
                capability.current_maturity_level is not None
                and capability.target_maturity_level is not None
            ),
            "risk_score": risk_score,
            "investment_priority_score": total_score,
            "priority_level": priority_level,
            "investment_need": investment_need,
            # Flat fields consumed by ROI Calculator modal
            "estimated_cost": investment_need["estimated_cost"],
            "has_recorded_cost": investment_need.get("has_recorded_cost", False),
            "timeframe": investment_need.get("timeframe_months", 12),
            # Initiative linkage
            "has_real_budget": has_real_budget,
            "linked_initiative_id": linked_initiative["initiative_id"] if linked_initiative else None,
            "linked_initiative_name": linked_initiative["initiative_name"] if linked_initiative else None,
            "linked_initiative_budget": linked_initiative["budget_allocated"] if linked_initiative else None,
            "recommendation": self._get_capability_recommendation(
                capability, total_score, coverage_count
            ),
        }

    def _calculate_strategic_score(self, capability) -> int:
        """Calculate strategic importance score (0 - 25 points)."""
        importance = (capability.strategic_importance or "").lower()

        if importance == "critical":
            return 25
        elif importance == "high":
            return 20
        elif importance == "medium":
            return 15
        elif importance == "low":
            return 10
        else:
            return 5

    def _calculate_coverage_score(self, coverage_count: int, capability) -> int:
        """Calculate coverage gap score (0 - 25 points)."""
        importance = (capability.strategic_importance or "").lower()

        # Critical capabilities need more coverage
        if importance == "critical":
            if coverage_count == 0:
                return 25  # No coverage - highest priority
            elif coverage_count == 1:
                return 20  # Single point of failure
            elif coverage_count == 2:
                return 10  # Minimal coverage
            else:
                return 5  # Adequate coverage
        elif importance == "high":
            if coverage_count == 0:
                return 20
            elif coverage_count == 1:
                return 15
            elif coverage_count == 2:
                return 5
            else:
                return 0
        else:
            if coverage_count == 0:
                return 15
            elif coverage_count == 1:
                return 5
            else:
                return 0

    def _calculate_maturity_score(self, capability) -> int:
        """Calculate maturity gap score (0 - 25 points).

        D-7: `or 1` / `or 3` fabricated a specific invented gap (e.g. "2")
        for a capability with no recorded maturity at all, feeding a
        computed investment-priority number from a made-up input. When
        either side is genuinely unassessed, this returns a documented
        NEUTRAL score (the midpoint of the 0-25 range) rather than
        pretending a real gap was measured -- the caller can distinguish
        "measured" from "assumed neutral" via `maturity_assessed` in the
        returned capability dict (see `_calculate_capability_score`).
        """
        current_maturity = capability.current_maturity_level
        target_maturity = capability.target_maturity_level

        if current_maturity is None or target_maturity is None:
            return 12  # neutral midpoint -- not a measured gap

        maturity_gap = target_maturity - current_maturity

        if maturity_gap >= 3:
            return 25  # Large maturity gap
        elif maturity_gap >= 2:
            return 20
        elif maturity_gap >= 1:
            return 15
        else:
            return 5  # Small or no gap

    def _calculate_risk_score(self, capability, app_mappings: List) -> int:
        """Calculate risk score (0 - 25 points)."""
        risk_score = 0

        # Single point of failure risk
        if len(app_mappings) == 1 and (capability.strategic_importance or "").lower() in [
            "critical",
            "high",
        ]:
            risk_score += 15

        # Technology debt risk
        high_debt_apps = [
            m
            for m in app_mappings
            if getattr(m, "technical_debt_score", None) is not None
            and m.technical_debt_score > 70
        ]
        if high_debt_apps:
            risk_score += 10

        # Compliance risk
        if hasattr(capability, "compliance_requirements") and capability.compliance_requirements:
            risk_score += 5

        return min(risk_score, 25)

    @staticmethod
    def _recorded_cost_for_apps(mapped_apps: List) -> float:
        """Sum the recorded annual cost of a capability's mapped applications.

        Reads each application's cost through the one canonical accessor
        (``application_cost_accessor.get_annual_cost``); an application with
        nothing recorded contributes nothing. Returns None when none of the
        mapped applications carries a recorded cost, so the caller can show an
        explained empty value rather than a zero that looks like a real figure.
        """
        total = None
        for component in mapped_apps or []:
            value = get_annual_cost_float(component)
            if value is not None:
                total = (total or 0.0) + value
        return total

    def _estimate_investment_need(
        self, capability, score: int,
        app_mappings: List = None,
        linked_initiative: Dict = None,
        recorded_cost: float = None,
    ) -> Dict:
        """The money figure for the capability, from recorded facts only.

        The figure is one of, in order of preference:
        - the sum of the recorded annual cost of the capability's mapped
          applications (the cost fact store, read through the canonical
          accessor), when any of them has a cost recorded; or
        - the linked StrategicInitiative's remaining budget, when the
          capability is funded by one (via RoadmapItem); or
        - None, meaning nothing is recorded yet. There is no computed or
          assumed figure: an unrecorded capability shows an explained empty
          value, not an invented number.
        """
        if app_mappings is None:
            app_mappings = []

        if len(app_mappings) == 0:
            timeframe = "12 - 18 months"
        elif len(app_mappings) == 1:
            timeframe = "6 - 12 months"
        else:
            timeframe = "3 - 6 months"

        base = {
            "currency": "USD",
            "timeframe": timeframe,
            "timeframe_months": _TIMEFRAME_MONTHS.get(timeframe, 12),
            "investment_type": "NEW" if len(app_mappings) == 0 else "IMPROVEMENT",
            "complexity": "HIGH" if score >= 80 else "MEDIUM" if score >= 60 else "LOW",
        }

        # --- Recorded annual cost of the mapped applications ---
        if recorded_cost is not None:
            base.update({
                "estimated_cost": recorded_cost,
                "cost_source": "recorded_application_cost",
                "has_recorded_cost": True,
                "has_real_budget": False,
                "budget_allocated": None,
                "budget_spent": None,
                "initiative_name": None,
            })
            return base

        # --- Linked initiative's remaining budget ---
        if linked_initiative and (linked_initiative.get("budget_allocated") or 0.0) > 0.0:
            base.update({
                "estimated_cost": linked_initiative["budget_remaining"],
                "cost_source": "linked_initiative_budget",
                "has_recorded_cost": False,
                "has_real_budget": True,
                "budget_allocated": linked_initiative["budget_allocated"],
                "budget_spent": linked_initiative["budget_spent"],
                "initiative_name": linked_initiative["initiative_name"],
            })
            return base

        # --- Nothing recorded: an explained empty value, never a figure ---
        base.update({
            "estimated_cost": None,
            "cost_source": None,
            "has_recorded_cost": False,
            "has_real_budget": False,
            "budget_allocated": None,
            "budget_spent": None,
            "initiative_name": None,
        })
        return base

    def _get_capability_recommendation(
        self, capability, score: int, coverage_count: int
    ) -> str:
        """Generate specific recommendation for the capability."""

        if coverage_count == 0:
            return (
                f"INVEST: Establish {capability.name} capability - no current application support"
            )
        elif coverage_count == 1 and (capability.strategic_importance or "").lower() in [
            "critical",
            "high",
        ]:
            return f"INVEST: Add redundancy for {capability.name} - single point of failure risk"
        elif score >= 70:
            return f"INVEST: High priority investment in {capability.name} - strategic importance with gaps"
        elif score >= 50:
            return f"CONSIDER: Moderate investment in {capability.name} - improvement opportunities"
        else:
            return f"MONITOR: {capability.name} - adequate coverage, monitor for changes"

    def _calculate_portfolio_metrics(self, capability_scores: List[Dict], apps_by_cap: Dict = None) -> Dict:
        """Calculate portfolio-level investment metrics.

        The headline total is the recorded annual cost of the organisation's
        mapped applications, each application counted once however many
        capabilities it supports, read through the one canonical cost
        accessor. It is None when no mapped application carries a recorded
        cost, so the screen shows an explained empty value rather than a
        total built from invented per-capability figures.
        """

        total_capabilities = len(capability_scores)
        critical_count = len([c for c in capability_scores if c["priority_level"] == "CRITICAL"])
        high_count = len([c for c in capability_scores if c["priority_level"] == "HIGH"])

        # Distinct mapped applications and their recorded annual cost.
        distinct_apps = {}
        for components in (apps_by_cap or {}).values():
            for component in components:
                distinct_apps[component.id] = component

        total_investment = None
        apps_with_cost = 0
        for component in distinct_apps.values():
            value = get_annual_cost_float(component)
            if value is not None:
                total_investment = (total_investment or 0.0) + value
                apps_with_cost += 1

        # Investment by priority: the recorded figures only, summed by the
        # priority band of the capability whose applications carry them.
        def _band_total(level):
            band = [
                c["investment_need"]["estimated_cost"]
                for c in capability_scores
                if c["priority_level"] == level
                and c["investment_need"].get("has_recorded_cost")
                and c["investment_need"]["estimated_cost"] is not None
            ]
            return sum(band) if band else None

        critical_investment = _band_total("CRITICAL")
        high_investment = _band_total("HIGH")

        # Average scores
        avg_strategic_score = (
            sum(c["strategic_score"] for c in capability_scores) / total_capabilities
            if total_capabilities > 0
            else 0
        )
        avg_coverage_score = (
            sum(c["coverage_score"] for c in capability_scores) / total_capabilities
            if total_capabilities > 0
            else 0
        )
        avg_maturity_score = (
            sum(c["maturity_score"] for c in capability_scores) / total_capabilities
            if total_capabilities > 0
            else 0
        )
        avg_risk_score = (
            sum(c["risk_score"] for c in capability_scores) / total_capabilities
            if total_capabilities > 0
            else 0
        )

        return {
            "total_capabilities": total_capabilities,
            "critical_priorities": critical_count,
            "high_priorities": high_count,
            "total_estimated_investment": total_investment,
            "has_recorded_investment": total_investment is not None,
            "mapped_application_count": len(distinct_apps),
            "applications_with_cost": apps_with_cost,
            "critical_investment": critical_investment,
            "high_investment": high_investment,
            "average_strategic_score": round(avg_strategic_score, 1),
            "average_coverage_score": round(avg_coverage_score, 1),
            "average_maturity_score": round(avg_maturity_score, 1),
            "average_risk_score": round(avg_risk_score, 1),
            "investment_currency": "USD",
        }

    def _generate_investment_recommendations(self, capability_scores: List[Dict]) -> List[Dict]:
        """Generate strategic investment recommendations."""

        recommendations = []

        # Top 5 critical investments
        critical_caps = [c for c in capability_scores if c["priority_level"] == "CRITICAL"][:5]

        for cap in critical_caps:
            recommendations.append(
                {
                    "type": "IMMEDIATE_INVESTMENT",
                    "priority": "CRITICAL",
                    "capability": cap["capability_name"],
                    "justification": f"Strategic {cap['strategic_importance']} capability with {cap['coverage_count']} applications",
                    "estimated_cost": cap["investment_need"]["estimated_cost"],
                    "timeframe": cap["investment_need"]["timeframe"],
                    "expected_roi": "HIGH"
                    if cap["strategic_importance"] == "critical"
                    else "MEDIUM",
                }
            )

        # Consolidation opportunities
        over_served = [c for c in capability_scores if c["coverage_count"] >= 5]
        if over_served:
            recommendations.append(
                {
                    "type": "CONSOLIDATION_OPPORTUNITY",
                    "priority": "HIGH",
                    "capability": f"{len(over_served)} capabilities",
                    "justification": "Multiple applications supporting same capabilities - consolidation opportunity",
                    "estimated_cost": "SAVINGS",
                    "timeframe": "6 - 12 months",
                    "expected_roi": "HIGH",
                }
            )

        # Single point of failures
        spf_caps = [
            c
            for c in capability_scores
            if c["coverage_count"] == 1 and c["strategic_importance"] in ["critical", "high"]
        ]
        if spf_caps:
            # Only the recorded figures contribute; when none of these
            # capabilities has a recorded cost, the figure stays empty rather
            # than defaulting to a made-up total.
            recorded = [
                c["investment_need"]["estimated_cost"]
                for c in spf_caps
                if c["investment_need"].get("has_recorded_cost")
                and c["investment_need"]["estimated_cost"] is not None
            ]
            recommendations.append(
                {
                    "type": "RISK_MITIGATION",
                    "priority": "HIGH",
                    "capability": f"{len(spf_caps)} capabilities",
                    "justification": "Single point of failure risks for critical capabilities",
                    "estimated_cost": sum(recorded) if recorded else None,
                    "timeframe": "6 - 12 months",
                    "expected_roi": "HIGH",
                }
            )

        return recommendations
