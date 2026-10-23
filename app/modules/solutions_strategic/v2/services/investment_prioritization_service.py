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

from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability
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

        # Batch-load all capability mappings scoped to this organization
        # UnifiedApplicationCapabilityMapping has no organization_id, so scope through
        # ApplicationComponent (TenantMixin, automatically filtered by current_org_id)
        mappings_by_cap = {}
        try:
            org_id = current_org_id()
            if org_id is not None:
                all_mappings = (
                    UnifiedApplicationCapabilityMapping.query
                    .join(
                        ApplicationComponent,
                        UnifiedApplicationCapabilityMapping.application_component_id == ApplicationComponent.id
                    )
                    .all()
                )
                for m in all_mappings:
                    mappings_by_cap.setdefault(m.unified_capability_id, []).append(m)
        except Exception:
            logger.debug("Could not batch-load capability mappings", exc_info=True)

        # Build capability → initiative map via RoadmapItem.linked_capabilities JSON
        linked_initiatives = self._get_linked_initiatives()

        # Analyze each capability for investment priority
        capability_scores = []
        for capability in capabilities:
            score_data = self._calculate_capability_score(
                capability, include_risk_analysis, mappings_by_cap, linked_initiatives
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
        portfolio_metrics = self._calculate_portfolio_metrics(capability_scores)

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
    ) -> Dict:
        """Calculate investment priority score for a single capability."""

        # Use pre-loaded mappings dict (batch) instead of per-capability query
        app_mappings = (mappings_by_cap or {}).get(capability.id, [])

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

        # Calculate estimated investment need — uses real initiative budget when available
        investment_need = self._estimate_investment_need(capability, total_score, app_mappings, linked_initiative)

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

    def _estimate_investment_need(
        self, capability, score: int,
        app_mappings: List = None,
        linked_initiative: Dict = None,
    ) -> Dict:
        """Estimate investment need for the capability.

        When the capability is linked to a StrategicInitiative (via RoadmapItem),
        ``linked_initiative`` contains the real budget figures from that initiative.
        Otherwise, a scoring-based estimate is produced and labelled accordingly.
        """
        if app_mappings is None:
            app_mappings = []

        # --- Use real initiative budget when available ---
        if linked_initiative and (linked_initiative.get("budget_allocated") or 0.0) > 0.0:
            remaining = linked_initiative["budget_remaining"]
            allocated = linked_initiative["budget_allocated"]
            spent = linked_initiative["budget_spent"]

            # Timeframe: derive from coverage (same logic as scoring-based)
            if len(app_mappings) == 0:
                timeframe = "12 - 18 months"
            elif len(app_mappings) == 1:
                timeframe = "6 - 12 months"
            else:
                timeframe = "3 - 6 months"

            return {
                "estimated_cost": remaining,
                "currency": "USD",
                "timeframe": timeframe,
                "timeframe_months": _TIMEFRAME_MONTHS.get(timeframe, 12),
                "investment_type": "IMPROVEMENT" if len(app_mappings) > 0 else "NEW",
                "complexity": "HIGH" if score >= 80 else "MEDIUM" if score >= 60 else "LOW",
                "has_real_budget": True,
                "budget_allocated": allocated,
                "budget_spent": spent,
                "initiative_name": linked_initiative["initiative_name"],
            }

        # --- Scoring-based estimate (no linked initiative) ---
        if score >= 80:
            base_cost = 500000
            complexity_multiplier = (
                1.5 if (capability.strategic_importance or "").lower() == "critical" else 1.0
            )
        elif len(app_mappings) == 1:
            base_cost = 200000
            complexity_multiplier = 1.2
        else:
            base_cost = 100000
            complexity_multiplier = 1.0

        estimated_cost = base_cost * complexity_multiplier

        if len(app_mappings) == 0:
            timeframe = "12 - 18 months"
        elif len(app_mappings) == 1:
            timeframe = "6 - 12 months"
        else:
            timeframe = "3 - 6 months"

        return {
            "estimated_cost": estimated_cost,
            "currency": "USD",
            "timeframe": timeframe,
            "timeframe_months": _TIMEFRAME_MONTHS.get(timeframe, 12),
            "investment_type": "NEW" if len(app_mappings) == 0 else "IMPROVEMENT",
            "complexity": (
                "HIGH" if complexity_multiplier > 1.2
                else "MEDIUM" if complexity_multiplier > 1.0
                else "LOW"
            ),
            "has_real_budget": False,
            "budget_allocated": None,
            "budget_spent": None,
            "initiative_name": None,
        }

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

    def _calculate_portfolio_metrics(self, capability_scores: List[Dict]) -> Dict:
        """Calculate portfolio-level investment metrics."""

        total_capabilities = len(capability_scores)
        critical_count = len([c for c in capability_scores if c["priority_level"] == "CRITICAL"])
        high_count = len([c for c in capability_scores if c["priority_level"] == "HIGH"])

        # Total estimated investment need
        total_investment = sum(c["investment_need"]["estimated_cost"] for c in capability_scores)

        # Investment by priority
        critical_investment = sum(
            c["investment_need"]["estimated_cost"]
            for c in capability_scores
            if c["priority_level"] == "CRITICAL"
        )
        high_investment = sum(
            c["investment_need"]["estimated_cost"]
            for c in capability_scores
            if c["priority_level"] == "HIGH"
        )

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
            recommendations.append(
                {
                    "type": "RISK_MITIGATION",
                    "priority": "HIGH",
                    "capability": f"{len(spf_caps)} capabilities",
                    "justification": "Single point of failure risks for critical capabilities",
                    "estimated_cost": sum(c["investment_need"]["estimated_cost"] for c in spf_caps),
                    "timeframe": "6 - 12 months",
                    "expected_roi": "HIGH",
                }
            )

        return recommendations
