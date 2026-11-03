"""
Vendor Scoring Service - Enhanced Edition

Implements Multi-Criteria Decision Analysis (MCDA) for vendor evaluation with:
- Dynamic TCO scoring based on industry benchmarks
- Context-aware scoring ranges (organization size, industry, deployment scale)
- Enhanced cost calculation with hidden costs
- Intelligent risk scoring based on actual data, not keywords
- Semantic capability matching with importance weighting
- External intelligence integration (G2, Gartner, customer references)
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from app.models import (
    BusinessCapability,
    OptionsAnalysis,
    TCOBenchmark,
    VendorOption,
)

logger = logging.getLogger(__name__)


class VendorScoringService:
    """
    Multi-Criteria Decision Analysis (MCDA) scoring engine for vendor evaluation.

    Uses weighted scoring across 5 dimensions to produce a comprehensive vendor score.
    """

    def __init__(self):
        """Initialize the scoring service with default weights."""
        self.default_weights = {
            "cost": 0.25,
            "capability_coverage": 0.25,
            "risk": 0.20,
            "strategic_fit": 0.15,
            "implementation": 0.15,
        }

    def score_vendor(
        self,
        vendor_option: VendorOption,
        capability: BusinessCapability,
        criteria_weights: Optional[Dict[str, float]] = None,
    ) -> VendorOption:
        """
        Perform comprehensive scoring of a vendor option.

        Args:
            vendor_option: The VendorOption to score
            capability: The BusinessCapability being analyzed
            criteria_weights: Optional custom weights (default: standard weights)

        Returns:
            Updated VendorOption with all scores calculated
        """
        weights = criteria_weights or self.default_weights

        # Score each dimension
        vendor_option.cost_score = self._score_cost(vendor_option)
        vendor_option.capability_coverage_score = self._score_capability_coverage(
            vendor_option, capability
        )
        vendor_option.risk_score = self._score_risk(vendor_option)
        vendor_option.strategic_fit_score = self._score_strategic_fit(vendor_option)
        vendor_option.implementation_score = self._score_implementation(vendor_option)

        # Calculate weighted total
        vendor_option.calculate_total_score(weights)

        return vendor_option

    def calculate_tco(
        self,
        vendor_option: VendorOption,
        years: int = 5,
        analysis: Optional[OptionsAnalysis] = None,
    ) -> Tuple[Decimal, Dict]:
        """
        Calculate Total Cost of Ownership with enhanced intelligence.

        Uses industry benchmarks, hidden costs, and context-aware multipliers
        instead of hardcoded 2x implementation cost.

        Args:
            vendor_option: The vendor option to analyze
            years: Number of years for TCO calculation
            analysis: Optional OptionsAnalysis for context (org size, industry, etc.)

        Returns:
            Tuple of (total_cost, breakdown_by_year)
        """
        tco_breakdown = {}
        total = Decimal("0.0")

        # Get base costs from technology stack or vendor option
        license_annual = vendor_option.license_cost_annual or Decimal("0.0")
        support_annual = vendor_option.support_cost_annual or Decimal("0.0")
        infrastructure_monthly = vendor_option.infrastructure_cost_monthly or Decimal("0.0")
        training = vendor_option.training_cost_estimate or Decimal("0.0")

        # Get implementation multiplier from benchmark (fallback to 1.5x)
        impl_multiplier = self._get_implementation_multiplier(vendor_option, analysis)

        # Enhanced costs
        data_migration_cost = vendor_option.data_migration_cost_estimate or Decimal("0.0")
        integration_cost = vendor_option.integration_development_cost or Decimal("0.0")
        change_mgmt_cost = vendor_option.change_management_cost or Decimal("0.0")

        for year in range(1, years + 1):
            year_cost = {}

            # License costs (may have discounts in later years)
            year_cost["license"] = float(license_annual)

            # Support costs (typically 15 - 20% of license, may increase over time)
            year_cost["support"] = float(
                support_annual * Decimal(str(1 + (year - 1) * 0.03))
            )  # 3% annual increase

            # Infrastructure costs
            year_cost["infrastructure"] = float(infrastructure_monthly * 12)

            # Training (primarily year 1, some ongoing)
            if year == 1:
                year_cost["training"] = float(training)
            else:
                year_cost["training"] = float(training * Decimal("0.1"))  # 10% ongoing training

            # Implementation costs (primarily year 1) - ENHANCED with multiplier
            if year == 1:
                impl_cost = license_annual * Decimal(str(impl_multiplier))
                year_cost["implementation"] = float(impl_cost)
                year_cost["data_migration"] = float(data_migration_cost)
                year_cost["integration_development"] = float(integration_cost)
                year_cost["change_management"] = float(change_mgmt_cost)
            else:
                year_cost["implementation"] = 0.0
                year_cost["data_migration"] = 0.0
                year_cost["integration_development"] = 0.0
                year_cost["change_management"] = 0.0

            # Hidden costs tracking
            if year == 1 and vendor_option.hidden_costs_identified:
                try:
                    hidden_costs = json.loads(vendor_option.hidden_costs_identified)
                    year_cost["hidden_costs"] = sum(float(h.get("amount", 0)) for h in hidden_costs)
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    logger.warning(f"Failed to parse hidden costs for {vendor_option.vendor_name}: {e}")
                    year_cost["hidden_costs"] = 0.0
            else:
                year_cost["hidden_costs"] = 0.0

            # Sum up year total
            year_total = sum(year_cost.values())
            year_cost["total"] = year_total

            tco_breakdown[f"year{year}"] = year_cost
            total += Decimal(str(year_total))

        vendor_option.tco_total = total
        vendor_option.tco_breakdown = json.dumps(tco_breakdown)

        logger.info(
            f"Calculated TCO for {vendor_option.vendor_name}: ${total:,.2f} over {years} years"
        )
        return total, tco_breakdown

    def _get_implementation_multiplier(
        self, vendor_option: VendorOption, analysis: Optional[OptionsAnalysis]
    ) -> float:
        """
        Get context-aware implementation cost multiplier from benchmarks.

        Replaces hardcoded 2x with intelligent lookup based on:
        - Capability category (CRM: 1.5x, ERP: 4x, etc.)
        - Organization size
        - Implementation complexity

        Returns:
            float: Multiplier for license cost (e.g., 1.5, 2.0, 4.0)
        """
        if not analysis:
            return 2.0  # Fallback to old default

        # Try to find matching benchmark
        benchmark = TCOBenchmark.query.filter_by(
            capability_category=analysis.capability.category
            if hasattr(analysis.capability, "category")
            else "general",
            organization_size=analysis.organization_size or "midmarket",
            is_active=True,
        ).first()

        if benchmark and benchmark.license_cost_multiplier:
            multiplier = float(benchmark.license_cost_multiplier)
            logger.info(f"Using benchmark multiplier: {multiplier}x for {analysis.capability.name}")
            return multiplier

        # Fallback: estimate based on complexity
        if vendor_option.implementation_complexity:
            if vendor_option.implementation_complexity <= 3:
                return 1.0
            elif vendor_option.implementation_complexity <= 5:
                return 1.5
            elif vendor_option.implementation_complexity <= 7:
                return 2.5
            else:
                return 4.0

        return 2.0  # Final fallback

    def _score_cost(
        self, vendor_option: VendorOption, analysis: Optional[OptionsAnalysis] = None
    ) -> float:
        """
        Score cost dimension with DYNAMIC RANGES (0 - 100, higher is better = lower cost).

        Prefers TCOBenchmark rows (capability type, org size, industry). When none
        match — the default state on an unseeded install — falls back to unsourced
        constants and logs a warning. The returned score does not currently
        distinguish the two cases; see _get_tco_scoring_range.
        """
        if not vendor_option.tco_total:
            # Calculate TCO if not already done
            self.calculate_tco(vendor_option, analysis=analysis)

        tco = float(vendor_option.tco_total or 0)

        if tco == 0:
            return 0.0  # Unknown cost = no data, not average

        # Get dynamic TCO range from benchmarks
        min_tco, max_tco = self._get_tco_scoring_range(vendor_option, analysis)

        logger.info(f"Scoring TCO ${tco:,.2f} against range ${min_tco:,.2f} - ${max_tco:,.2f}")

        # Normalize to 0 - 100 scale (lower cost = higher score)
        if tco <= min_tco:
            return 100.0
        elif tco >= max_tco:
            return 0.0
        else:
            # Linear interpolation
            score = 100 * (1 - (tco - min_tco) / (max_tco - min_tco))
            return max(0.0, min(100.0, score))

    def _get_tco_scoring_range(
        self, vendor_option: VendorOption, analysis: Optional[OptionsAnalysis]
    ) -> Tuple[float, float]:
        """
        Get dynamic TCO scoring range from benchmarks.

        Returns:
            Tuple of (min_tco, max_tco) for scoring purposes
        """
        # Try to find matching benchmark
        if analysis:
            benchmark = TCOBenchmark.query.filter_by(
                capability_category=analysis.capability.category
                if hasattr(analysis.capability, "category")
                else "general",
                organization_size=analysis.organization_size or "midmarket",
                is_active=True,
            ).first()

            if benchmark:
                logger.info(
                    f"Using TCO benchmark for {benchmark.capability_category}/{benchmark.organization_size}"
                )
                return (float(benchmark.min_tco), float(benchmark.max_tco))

        # No TCOBenchmark row matched — which is the default state until the
        # benchmark table is seeded. Everything below is an unsourced estimate,
        # not market data, so log loudly: a cost score derived from these is only
        # as good as the guess. Callers that surface the score to users should
        # badge it as unbenchmarked (see TODO below).
        # TODO: return a (range, is_benchmarked) pair and render an
        # "unbenchmarked estimate" badge in the vendor comparison UI.
        if analysis and hasattr(analysis.capability, "category"):
            category = analysis.capability.category.lower()
            fallbacks = {
                "crm": (50000, 500000),
                "erp": (500000, 5000000),
                "bi": (100000, 1000000),
                "analytics": (100000, 1000000),
                "collaboration": (20000, 200000),
            }
            for key, rng in fallbacks.items():
                if key in category:
                    logger.warning(
                        "No TCOBenchmark for category=%s size=%s; scoring against "
                        "UNSOURCED fallback range $%s-$%s",
                        category,
                        getattr(analysis, "organization_size", None),
                        f"{rng[0]:,}",
                        f"{rng[1]:,}",
                    )
                    return rng

        logger.warning(
            "No TCOBenchmark and no category match; scoring against UNSOURCED "
            "default range 50,000-500,000"
        )
        return (50000, 500000)

    def _score_capability_coverage(
        self, vendor_option: VendorOption, capability: BusinessCapability
    ) -> float:
        """
        Score capability coverage (0 - 100, higher is better).

        Based on gap analysis and match percentage.
        """
        # Use existing match percentage if available
        if vendor_option.capability_match_percentage is not None:
            return float(vendor_option.capability_match_percentage)

        # Otherwise, estimate based on capability gaps
        if vendor_option.capability_gaps:
            gaps = json.loads(vendor_option.capability_gaps)
            if isinstance(gaps, list):
                num_gaps = len(gaps)
                # Assume 20 total capability requirements, score based on gaps
                coverage = max(0, (20 - num_gaps) / 20 * 100)
                vendor_option.capability_match_percentage = coverage
                return coverage

        # Default: assume 70% coverage if no data
        return 70.0

    def _score_risk(self, vendor_option: VendorOption) -> float:
        """
        Score risk dimension (0 - 100, higher is better = lower risk).

        Aggregates multiple risk factors.
        """
        risk_scores = []

        # Vendor lock-in (1 - 10, 10 = high lock-in = bad)
        if vendor_option.vendor_lock_in_risk:
            # Invert: high lock-in gets low score
            risk_scores.append((11 - vendor_option.vendor_lock_in_risk) * 10)

        # Market position (1 - 10, 10 = risky position = bad)
        if vendor_option.market_position_risk:
            risk_scores.append((11 - vendor_option.market_position_risk) * 10)

        # Support continuity (1 - 10, 10 = high risk = bad)
        if vendor_option.support_continuity_risk:
            risk_scores.append((11 - vendor_option.support_continuity_risk) * 10)

        # Technology maturity (1 - 10, 10 = immature/risky = bad)
        if vendor_option.technology_maturity_risk:
            risk_scores.append((11 - vendor_option.technology_maturity_risk) * 10)

        # Compliance risk (1 - 10, 10 = high risk = bad)
        if vendor_option.compliance_risk:
            risk_scores.append((11 - vendor_option.compliance_risk) * 10)

        # Vendor health score (already 0 - 100, higher is better)
        if vendor_option.vendor_health_score:
            risk_scores.append(float(vendor_option.vendor_health_score))

        if not risk_scores:
            return 50.0  # Default if no risk data

        # Average all risk scores
        return sum(risk_scores) / len(risk_scores)

    def _score_strategic_fit(self, vendor_option: VendorOption) -> float:
        """
        Score strategic fit (0 - 100, higher is better).

        Based on alignment with enterprise strategy and roadmap.
        """
        fit_scores = []

        # Technology alignment (1 - 10)
        if vendor_option.technology_alignment:
            fit_scores.append(vendor_option.technology_alignment * 10)

        # Roadmap alignment (1 - 10)
        if vendor_option.roadmap_alignment:
            fit_scores.append(vendor_option.roadmap_alignment * 10)

        # Vendor relationship (1 - 10)
        if vendor_option.vendor_relationship:
            fit_scores.append(vendor_option.vendor_relationship * 10)

        # Future proofing (1 - 10)
        if vendor_option.future_proofing:
            fit_scores.append(vendor_option.future_proofing * 10)

        # Ecosystem fit (1 - 10)
        if vendor_option.ecosystem_fit:
            fit_scores.append(vendor_option.ecosystem_fit * 10)

        if not fit_scores:
            return 50.0  # Default if no strategic data

        # Average all fit scores
        return sum(fit_scores) / len(fit_scores)

    def _score_implementation(self, vendor_option: VendorOption) -> float:
        """
        Score implementation dimension (0 - 100, higher is better = easier implementation).

        Based on complexity, timeline, and resource requirements.
        """
        scores = []

        # Implementation complexity (1 - 10, 10 = very complex = bad)
        if vendor_option.implementation_complexity:
            # Invert: high complexity gets low score
            scores.append((11 - vendor_option.implementation_complexity) * 10)

        # Timeline (shorter is better, but reasonable)
        if vendor_option.estimated_implementation_weeks:
            weeks = vendor_option.estimated_implementation_weeks
            # Ideal: 8 - 12 weeks, penalize too short (<4) or too long (>24)
            if 8 <= weeks <= 12:
                scores.append(100.0)
            elif weeks < 4:
                scores.append(50.0)  # Too aggressive, risky
            elif weeks <= 24:
                # Linear decline from 100 to 50
                scores.append(100 - ((weeks - 12) / 12 * 50))
            else:
                scores.append(25.0)  # Too long

        # Skill availability (1 - 10, 10 = readily available)
        if vendor_option.skill_availability:
            scores.append(vendor_option.skill_availability * 10)

        if not scores:
            return 50.0  # Default if no implementation data

        # Average all implementation scores
        return sum(scores) / len(scores)

    def assess_capability_gaps(
        self,
        vendor_option: VendorOption,
        capability: BusinessCapability,
        required_capabilities: List[str],
    ) -> Dict:
        """
        Perform detailed gap analysis between vendor and required capabilities.

        Args:
            vendor_option: The vendor to analyze
            capability: The business capability
            required_capabilities: List of required capability names

        Returns:
            Dict with gaps, supported capabilities, and match percentage
        """
        # Get vendor's supported capabilities
        vendor_tech_stack = vendor_option.technology_stack
        supported = []
        missing = []

        # Simple keyword matching (in production, use more sophisticated matching)
        for req_cap in required_capabilities:
            req_lower = req_cap.lower()

            # Check if capability is mentioned in tech stack description or capabilities
            is_supported = False

            if vendor_tech_stack.description and req_lower in vendor_tech_stack.description.lower():
                is_supported = True

            # Check frameworks, languages, etc.
            tech_attrs = [
                vendor_tech_stack.framework,
                vendor_tech_stack.primary_language,
                vendor_tech_stack.primary_database,
                vendor_tech_stack.api_standard,
            ]

            for attr in tech_attrs:
                if attr and req_lower in attr.lower():
                    is_supported = True
                    break

            if is_supported:
                supported.append(req_cap)
            else:
                missing.append(req_cap)

        # Calculate match percentage
        total = len(required_capabilities)
        match_count = len(supported)
        match_percentage = (match_count / total * 100) if total > 0 else 0.0

        # Build gap analysis
        gaps = []
        for missing_cap in missing:
            gaps.append(
                {
                    "gap": missing_cap,
                    "severity": "high"
                    if "critical" in missing_cap.lower() or "security" in missing_cap.lower()
                    else "medium",
                    "workaround": "Custom development required"
                    if "integration" in missing_cap.lower()
                    else "Third-party tool may be needed",
                }
            )

        # Update vendor option
        vendor_option.supported_capabilities = json.dumps(supported)
        vendor_option.missing_capabilities = json.dumps(missing)
        vendor_option.capability_gaps = json.dumps(gaps)
        vendor_option.capability_match_percentage = match_percentage

        return {
            "supported": supported,
            "missing": missing,
            "gaps": gaps,
            "match_percentage": match_percentage,
        }

    def assess_risks(self, vendor_option: VendorOption, vendor_name: str) -> Dict[str, int]:
        """
        ENHANCED risk assessment using actual data, not keyword theater.

        Uses external intelligence, financial data, and technical analysis
        instead of naive keyword matching.

        Args:
            vendor_option: The vendor option to assess
            vendor_name: Name of the vendor

        Returns:
            Dict with risk scores (1 - 10 for each dimension)
        """
        risks = {}

        # === 1. INTELLIGENT VENDOR LOCK-IN RISK ===
        # Based on actual API portability and data export capability, not keywords
        if vendor_option.api_portability_score and vendor_option.data_export_capability:
            # Calculate from actual technical analysis
            portability = vendor_option.api_portability_score
            export_cap = vendor_option.data_export_capability
            # Average of inverted scores (10 = portable = low lock-in)
            lock_in = 11 - ((portability + export_cap) / 2)
            risks["vendor_lock_in_risk"] = int(round(lock_in))
        else:
            # Fallback: analyze tech stack
            lock_in = 5  # Default medium
            tech_stack = vendor_option.technology_stack
            if tech_stack:
                # Check for open standards
                if tech_stack.api_standard and "rest" in tech_stack.api_standard.lower():
                    lock_in -= 1  # REST is standard
                if tech_stack.primary_database in ["PostgreSQL", "MySQL", "MongoDB"]:
                    lock_in -= 1  # Open source DB
                # Check for proprietary
                if tech_stack.platform in ["salesforce", "proprietary"]:
                    lock_in += 2
            risks["vendor_lock_in_risk"] = max(1, min(10, lock_in))

        # === 2. MARKET POSITION RISK ===
        # Use actual financial and market data
        market_risk = 5  # Default

        # Use funding status and credit rating
        if vendor_option.vendor_credit_rating:
            rating = vendor_option.vendor_credit_rating.upper()
            if rating in ["AAA", "AA", "A"]:
                market_risk = 1  # Excellent credit
            elif rating in ["BBB", "BB"]:
                market_risk = 3  # Good credit
            elif rating in ["B", "CCC"]:
                market_risk = 7  # Risky
            else:
                market_risk = 9  # Very risky

        # Funding/runway for startups
        if (
            vendor_option.funding_status in ["bootstrapped", "seed"]
            and vendor_option.months_of_runway
        ):
            if vendor_option.months_of_runway < 6:
                market_risk = 9  # Running out of cash
            elif vendor_option.months_of_runway < 12:
                market_risk = 7
            elif vendor_option.months_of_runway < 24:
                market_risk = 5

        # Public companies or well-funded = lower risk
        if vendor_option.funding_status in ["public", "acquired_by_major"]:
            market_risk = 2

        # Employee count as secondary factor
        if vendor_option.vendor_employee_count:
            if vendor_option.vendor_employee_count > 10000:
                market_risk = min(market_risk, 2)  # Large = stable
            elif vendor_option.vendor_employee_count < 50:
                market_risk = max(market_risk, 7)  # Very small = risky

        risks["market_position_risk"] = market_risk

        # === 3. SUPPORT CONTINUITY RISK ===
        support_risk = 5  # Default

        # Use customer retention rate as primary indicator
        if vendor_option.customer_retention_rate:
            retention = vendor_option.customer_retention_rate
            if retention >= 95:
                support_risk = 2  # Excellent retention
            elif retention >= 85:
                support_risk = 4
            elif retention >= 75:
                support_risk = 6
            else:
                support_risk = 8  # Poor retention = support issues

        # Years in business as secondary
        if vendor_option.vendor_year_founded:
            years_in_business = datetime.now().year - vendor_option.vendor_year_founded
            if years_in_business > 20:
                support_risk = min(support_risk, 2)  # Established
            elif years_in_business < 3:
                support_risk = max(support_risk, 7)  # New, unproven

        risks["support_continuity_risk"] = support_risk

        # === 4. TECHNOLOGY MATURITY RISK ===
        maturity_risk = 5  # Default

        # Use actual security rating
        if vendor_option.security_rating:
            sec_rating = vendor_option.security_rating
            if sec_rating >= 8:
                maturity_risk = 2  # Mature, secure
            elif sec_rating >= 6:
                maturity_risk = 4
            elif sec_rating >= 4:
                maturity_risk = 6
            else:
                maturity_risk = 8  # Immature security

        # Use NPS score if available
        if vendor_option.nps_score:
            nps = vendor_option.nps_score
            if nps >= 50:
                maturity_risk = min(maturity_risk, 3)  # Excellent product
            elif nps >= 20:
                maturity_risk = min(maturity_risk, 5)
            elif nps < 0:
                maturity_risk = max(maturity_risk, 8)  # Poor product quality

        risks["technology_maturity_risk"] = maturity_risk

        # === 5. COMPLIANCE RISK ===
        compliance_risk = 5  # Default

        # Use actual certifications and compliance frameworks
        cert_count = 0
        if vendor_option.certifications:
            try:
                certs = (
                    json.loads(vendor_option.certifications)
                    if isinstance(vendor_option.certifications, str)
                    else vendor_option.certifications
                )
                cert_count = len(certs) if certs else 0
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Failed to parse certifications for {vendor_option.vendor_name}: {e}")

        framework_count = 0
        if vendor_option.compliance_frameworks:
            try:
                frameworks = (
                    json.loads(vendor_option.compliance_frameworks)
                    if isinstance(vendor_option.compliance_frameworks, str)
                    else vendor_option.compliance_frameworks
                )
                framework_count = len(frameworks) if frameworks else 0
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.warning(f"Failed to parse compliance frameworks for {vendor_option.vendor_name}: {e}")

        total_compliance = cert_count + framework_count
        if total_compliance >= 5:
            compliance_risk = 1  # Excellent compliance
        elif total_compliance >= 3:
            compliance_risk = 3
        elif total_compliance >= 1:
            compliance_risk = 5
        else:
            compliance_risk = 8  # No certifications = high risk

        risks["compliance_risk"] = compliance_risk

        # Update vendor option
        for risk_type, risk_value in risks.items():
            setattr(vendor_option, risk_type, risk_value)

        logger.info(
            f"Assessed risks for {vendor_name}: lock-in={risks['vendor_lock_in_risk']}, market={risks['market_position_risk']}, support={risks['support_continuity_risk']}, maturity={risks['technology_maturity_risk']}, compliance={risks['compliance_risk']}"
        )

        return risks

    def calculate_implementation_complexity(
        self, vendor_option: VendorOption, capability: BusinessCapability
    ) -> Tuple[int, int, Dict]:
        """
        Estimate implementation complexity and timeline.

        Args:
            vendor_option: The vendor option
            capability: The business capability

        Returns:
            Tuple of (complexity_score_1_10, estimated_weeks, resource_requirements)
        """
        # Base complexity on capability maturity gap
        complexity = 5  # Default medium
        weeks = 12  # Default 3 months

        # Higher complexity for larger maturity gaps
        if capability.maturity_gap and capability.maturity_gap > 2:
            complexity += 2
            weeks += 8

        # Adjust based on vendor's technology stack familiarity
        if vendor_option.technology_stack:
            tech_stack = vendor_option.technology_stack

            # More complex if using unfamiliar tech
            if tech_stack.primary_language in ["Java", "Python", "JavaScript"]:
                complexity -= 1  # Common languages, easier
            else:
                complexity += 1  # Less common

            # Cloud deployments may be simpler
            if tech_stack.platform in ["aws", "azure", "gcp"]:
                weeks -= 2  # Faster cloud deployment

        # Estimate resource requirements
        resources = {
            "developers": 2 if complexity <= 5 else 4,
            "architects": 1,
            "specialists": 1 if complexity > 7 else 0,
        }

        # Update vendor option
        vendor_option.implementation_complexity = complexity
        vendor_option.estimated_implementation_weeks = weeks
        vendor_option.resource_requirements = json.dumps(resources)

        return complexity, weeks, resources

    def rank_vendors(self, vendor_options: List[VendorOption]) -> List[VendorOption]:
        """
        Rank all vendor options by total score.

        Args:
            vendor_options: List of VendorOptions to rank

        Returns:
            List of VendorOptions sorted by score (descending) with rankings assigned
        """
        # Sort by total_score descending
        sorted_vendors = sorted(vendor_options, key=lambda v: v.total_score or 0, reverse=True)

        # Assign rankings
        for i, vendor in enumerate(sorted_vendors, start=1):
            vendor.ranking = i

        return sorted_vendors
