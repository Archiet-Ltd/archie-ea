"""
-> app.modules.architecture.services.governance_service

Solution Architect Orchestrator Service

Coordinates multiple analysis services to provide comprehensive
solution options analysis for solution architects.

Combines:
- Buy/Build/Reuse analysis
- Existing application analysis
- Vendor research and comparison
- Capability gap analysis
- Cost modeling and risk assessment
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app import db
from app.models import ApplicationComponent, BusinessCapability, VendorProduct  # dead-code-ok
from app.modules.architecture.services.options_analysis_service import (
    OptionsAnalysisService as ArchimateOptionsService,
)
from app.services.application_cost_accessor import get_annual_cost_float
from app.services.unified_duplicate_service import UnifiedDuplicateService
from app.services.vendor_analysis.options_analysis_service import (
    OptionsAnalysisService as VendorOptionsService,
)
from app.services.vendor_analysis.vendor_research_service import VendorResearchService

logger = logging.getLogger(__name__)


class SolutionArchitectOrchestrator:
    """
    Main orchestrator for solution architect workspace.

    Provides intelligent, context-aware solution recommendations by
    analyzing multiple dimensions in parallel.
    """

    def __init__(self):
        # Lazy import to break circular dependency
        from app.services.capability_gap_service import CapabilityGapAnalysisService
        
        self.archimate_options = ArchimateOptionsService()
        self.vendor_options = VendorOptionsService()
        self.duplicate_service = UnifiedDuplicateService()
        self.gap_service = CapabilityGapAnalysisService()
        self.vendor_research = VendorResearchService()

    def analyze_problem(
        self,
        problem_description: str,
        capability_id: Optional[int] = None,
        budget_min: Optional[Decimal] = None,
        budget_max: Optional[Decimal] = None,
        timeline_months: Optional[int] = None,
        user_count: Optional[int] = None,
        is_critical: bool = False,
        organization_size: Optional[str] = None,
        industry_vertical: Optional[str] = None,
        existing_constraints: Optional[List[str]] = None,
        compliance_requirements: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Perform comprehensive solution options analysis.

        Runs multiple analyses in parallel and synthesizes results into
        actionable recommendations.

        Args:
            problem_description: Description of the problem/need
            capability_id: Optional BusinessCapability ID
            budget_min: Minimum budget
            budget_max: Maximum budget
            timeline_months: Timeline constraint in months
            user_count: Expected number of users
            is_critical: Whether this is business-critical
            organization_size: smb, midmarket, enterprise
            industry_vertical: Industry sector
            existing_constraints: List of constraints (e.g., "must integrate with SAP")
            compliance_requirements: List of compliance needs (e.g., "GDPR", "SOC2")

        Returns:
            Comprehensive analysis results with ranked options
        """
        try:
            logger.info(f"Starting solution analysis for: {problem_description[:100]}")

            # Build context for analysis
            context = {
                "budget": f"£{budget_min or 0} - £{budget_max or 999999}",
                "timeline": f"{timeline_months or 12} months",
                "organization_size": organization_size or "midmarket",
                "industry": industry_vertical or "general",
                "user_count": user_count or 100,
                "is_critical": is_critical,
                "constraints": existing_constraints or [],
                "compliance": compliance_requirements or [],
            }

            # Phase 1: Buy/Build/Reuse AI Analysis
            logger.info("Phase 1: Running Buy/Build/Reuse analysis...")
            requirement = {
                "name": problem_description[:100],
                "description": problem_description,
                "category": "Functional",
            }
            buy_build_analysis = self.archimate_options.analyze_requirement_options(
                requirement=requirement, context=context
            )

            # Phase 2: Find existing applications that might help
            logger.info("Phase 2: Analyzing existing applications...")
            existing_apps = self._analyze_existing_applications(problem_description, capability_id)

            # Phase 3: Research potential vendors
            logger.info("Phase 3: Researching vendor solutions...")
            vendor_options = self._research_vendor_options(
                problem_description, capability_id, context
            )

            # Phase 4: Assess capability gaps
            logger.info("Phase 4: Assessing capability gaps...")
            gap_analysis = self._assess_capability_gaps(capability_id, existing_apps)

            # Synthesize all results into recommendations
            logger.info("Synthesizing results...")
            recommendations = self._synthesize_recommendations(
                buy_build_analysis=buy_build_analysis,
                existing_apps=existing_apps,
                vendor_options=vendor_options,
                gap_analysis=gap_analysis,
                context=context,
            )

            result = {
                "success": True,
                "problem_description": problem_description,
                "analysis_date": datetime.utcnow().isoformat(),
                "recommendations": recommendations,
                "buy_build_analysis": buy_build_analysis,
                "existing_applications": existing_apps,
                "vendor_options": vendor_options,
                "gap_analysis": gap_analysis,
                "context": context,
            }

            logger.info(
                f"Analysis complete. Top recommendation: {recommendations[0]['option_type']}"
            )
            return result

        except Exception as e:
            logger.error(f"Error in solution analysis: {e}", exc_info=True)
            return {"success": False, "error": str(e), "problem_description": problem_description}

    def _analyze_existing_applications(
        self, problem_description: str, capability_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Find existing applications that might address the need.

        Returns application matches with coverage estimates.
        """
        try:
            # Query for potentially relevant applications
            query = db.session.query(ApplicationComponent).filter(
                ApplicationComponent.lifecycle_status.in_(["active", "production", "development"])
            )

            # If capability specified, filter by it
            if capability_id:
                # Note: Would need to join through application_capability_mappings
                # For now, get all active apps
                pass

            apps = query.limit(20).all()

            # Calculate relevance scores (simple keyword matching for now)
            keywords = set(problem_description.lower().split())
            relevant_apps = []

            for app in apps:
                score = 0
                app_text = f"{app.name} {app.description or ''}".lower()

                # Count keyword matches
                matches = sum(1 for kw in keywords if kw in app_text)
                score = min(100, (matches / len(keywords)) * 100) if keywords else 0

                if score > 20:  # Only include if somewhat relevant
                    relevant_apps.append(
                        {
                            "id": app.id,
                            "name": app.name,
                            "description": app.description,
                            "relevance_score": round(score, 1),
                            "status": app.lifecycle_status or app.deployment_status,
                            "vendor": app.vendor_name or "Internal",
                            "annual_cost": get_annual_cost_float(app) or 0,
                            "users": app.user_count or 0,
                        }
                    )

            # Sort by relevance
            relevant_apps.sort(key=lambda x: x["relevance_score"], reverse=True)

            # Calculate potential consolidation savings
            if len(relevant_apps) > 1:
                total_cost = sum(app["annual_cost"] for app in relevant_apps)
                estimated_savings = total_cost * 0.3  # Assume 30% consolidation savings
            else:
                estimated_savings = 0

            return {
                "found_count": len(relevant_apps),
                "applications": relevant_apps[:5],  # Top 5
                "estimated_savings": round(estimated_savings, 2),
                "recommendation": "reuse" if relevant_apps else "new_solution",
            }

        except Exception as e:
            logger.error(f"Error analyzing existing apps: {e}")
            return {
                "found_count": 0,
                "applications": [],
                "estimated_savings": 0,
                "recommendation": "new_solution",
                "error": str(e),
            }

    def _research_vendor_options(
        self, problem_description: str, capability_id: Optional[int], context: Dict
    ) -> Dict[str, Any]:
        """
        Research and score potential vendor solutions.

        Returns top vendor options with scoring.
        """
        try:
            # Extract meaningful keywords — skip stopwords and short noise words
            _stopwords = {
                "we", "i", "a", "an", "the", "and", "or", "but", "in", "on",
                "at", "to", "for", "of", "with", "is", "are", "our", "has",
                "have", "that", "this", "need", "needs", "want", "wants",
                "use", "used", "new", "system", "solution", "platform",
                "application", "app", "some", "also", "which", "they",
            }
            _raw = [w.strip(".,;:!?'\"") for w in problem_description.lower().split()]
            keywords = [w for w in _raw if len(w) >= 4 and w not in _stopwords][:8]
            if not keywords:  # Fallback: any word ≥ 3 chars
                keywords = [w for w in _raw if len(w) >= 3][:5]

            # Query vendor products
            query = db.session.query(VendorProduct)

            # Simple keyword search in product names/descriptions
            search_filters = []
            for kw in keywords:
                search_filters.append(VendorProduct.name.ilike(f"%{kw}%"))
                search_filters.append(VendorProduct.description.ilike(f"%{kw}%"))

            if search_filters:
                from sqlalchemy import or_

                query = query.filter(or_(*search_filters))

            products = query.limit(10).all()

            vendor_options = []
            for product in products:
                # Score from real data only — capability match, pricing if available
                score = None
                estimated_cost = None
                try:
                    from app.models.vendor.vendor_organization import VendorProductPricing
                    # tenant-scoping-ok: vendor reference/catalog data (product-scoped or global catalog stats), not tenant-owned.
                    pricing = db.session.query(VendorProductPricing).filter(
                        VendorProductPricing.vendor_product_id == product.id
                    ).first()
                    if pricing and pricing.list_price_annual:
                        estimated_cost = float(pricing.list_price_annual)
                        score = max(0, min(100, 100 - (estimated_cost / 10000)))  # Rough fit score
                except Exception as e:
                    logger.debug("Vendor pricing lookup skipped for product %s: %s", product.id, e)

                vendor_options.append(
                    {
                        "vendor_id": product.vendor_organization_id,
                        "vendor_name": product.vendor_organization.name
                        if product.vendor_organization
                        else "Unknown",
                        "product_id": product.id,
                        "product_name": product.name,
                        "description": product.description,
                        "score": score,
                        "deployment_model": product.deployment_model or "cloud",
                        "estimated_cost": estimated_cost,
                        "strengths": [],
                        "weaknesses": [],
                    }
                )

            # Sort by score (None last)
            vendor_options.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)), reverse=False)

            return {
                "found_count": len(vendor_options),
                "top_vendors": vendor_options[:5],
                "recommendation": "buy" if vendor_options else "build",
            }

        except Exception as e:
            logger.error(f"Error researching vendors: {e}")
            return {"found_count": 0, "top_vendors": [], "recommendation": "build", "error": str(e)}

    def _assess_capability_gaps(
        self, capability_id: Optional[int], existing_apps: Dict
    ) -> Dict[str, Any]:
        """
        Assess capability coverage gaps.

        Returns gap analysis and coverage percentage.
        """
        try:
            if not capability_id:
                return {
                    "has_gaps": True,
                    "coverage_percentage": 0,
                    "gaps": ["No capability specified"],
                }

            # Get capability
            capability = db.session.query(BusinessCapability).get(capability_id)
            if not capability:
                return {
                    "has_gaps": True,
                    "coverage_percentage": 0,
                    "gaps": ["Capability not found"],
                }

            # Analyze coverage from actual app-to-capability mappings when available
            app_count = existing_apps.get("found_count", 0)
            coverage = 0
            if app_count > 0:
                apps = existing_apps.get("applications", [])
                if apps and capability_id:
                    # Count apps that map to this capability
                    try:
                        from app.models.application_capability import ApplicationCapabilityMapping
                        app_ids = [a.get("id") for a in apps if a.get("id")]
                        if app_ids:
                            # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                            mapped = db.session.query(ApplicationCapabilityMapping).filter(
                                ApplicationCapabilityMapping.business_capability_id == capability_id,
                                ApplicationCapabilityMapping.application_component_id.in_(app_ids),
                            ).count()
                            coverage = round((mapped / len(app_ids)) * 100, 1)
                        else:
                            coverage = round(min(100, app_count * 25), 1)
                    except Exception as _gap_err:
                        logger.debug("Capability gap DB lookup skipped: %s", _gap_err)
                        coverage = round(min(100, app_count * 25), 1)  # Fallback from count only
                else:
                    coverage = round(min(100, app_count * 25), 1)

            gaps = []
            if coverage < 100:
                gaps.append(f"{100 - coverage}% functionality not covered")
            if app_count == 0:
                gaps.append("No existing applications support this capability")

            return {
                "capability_name": capability.name,
                "has_gaps": coverage < 100,
                "coverage_percentage": coverage,
                "gaps": gaps,
            }

        except Exception as e:
            logger.error(f"Error assessing gaps: {e}")
            return {"has_gaps": True, "coverage_percentage": 0, "gaps": [str(e)]}

    def _synthesize_recommendations(
        self,
        buy_build_analysis: Dict,
        existing_apps: Dict,
        vendor_options: Dict,
        gap_analysis: Dict,
        context: Dict,
    ) -> List[Dict[str, Any]]:
        """
        Synthesize all analyses into ranked recommendations.

        Returns list of recommendations sorted by score.
        """
        recommendations = []

        # Extract timeline and compliance context for use across all options
        _timeline_ctx = context.get("timeline", "12 months")
        try:
            _base_timeline = int(_timeline_ctx.split()[0])
        except (ValueError, IndexError, AttributeError):
            _base_timeline = 12

        _compliance = context.get("compliance", [])
        _compliance_count = len(_compliance)
        _constraints = context.get("constraints", [])

        # Build context-aware pro/con addons from compliance and constraints
        def _compliance_pros(option_type: str) -> list:
            """Return compliance-specific pro bullets relevant to option."""
            extras = []
            if _compliance:
                labels = ", ".join(_compliance[:3])
                if option_type == "BUY":
                    extras.append(f"Many vendors are pre-certified for {labels}")
                elif option_type == "REUSE":
                    extras.append(f"Existing controls can be extended for {labels}")
                elif option_type == "BUILD":
                    extras.append(f"Architecture can be designed to meet {labels} natively")
            return extras

        def _constraints_cons(option_type: str) -> list:
            """Return integration constraint risk bullets."""
            if not _constraints:
                return []
            constraint_str = "; ".join(str(c) for c in _constraints[:2])
            if option_type == "BUY":
                return [f"Integration required: {constraint_str[:80]}"]
            if option_type == "BUILD":
                return [f"Custom connectors needed for: {constraint_str[:80]}"]
            return []

        # Option 1: REUSE (if existing apps found with good coverage)
        if existing_apps.get("found_count", 0) > 0:
            apps = existing_apps.get("applications", [])
            coverage = gap_analysis.get("coverage_percentage", 0)

            reuse_score = 50 + (coverage / 2)  # Max 100 if 100% coverage
            reuse_score += min(20, len(apps) * 5)  # Bonus for multiple options

            reuse_timeline = max(1, round(_base_timeline * 0.35))

            reuse_pros = [
                "Fastest time to value",
                "Leverages existing investment",
                f'Potential £{existing_apps.get("estimated_savings", 0):.0f}/year savings',
                "Low integration risk",
            ] + _compliance_pros("REUSE")

            reuse_cons = [
                f"{100 - coverage}% gap requires new development"
                if coverage < 100
                else "Minor gaps",
                "May require legacy system updates",
                "Limited to existing architecture",
            ] + _constraints_cons("REUSE")

            recommendations.append(
                {
                    "option_type": "REUSE",
                    "score": round(min(100, reuse_score), 1),
                    "estimated_cost": (
                        sum(a.get("annual_cost", 0) for a in apps) * 0.2
                        if any(a.get("annual_cost") for a in apps)
                        else None
                    ),
                    "timeline_months": reuse_timeline,
                    "risk_level": "LOW",
                    "description": f"Extend {len(apps)} existing application(s) to cover the gap",
                    "pros": reuse_pros,
                    "cons": reuse_cons,
                    "details": {
                        "applications": apps,
                        "coverage": f"{coverage}%",
                        "gap_coverage_needed": 100 - coverage,
                    },
                }
            )

        # Option 2: BUY (if vendors found)
        if vendor_options.get("found_count", 0) > 0:
            vendors = vendor_options.get("top_vendors", [])
            vendor_scores = [v.get("score") for v in vendors if v.get("score") is not None]
            avg_vendor_score = (
                sum(vendor_scores) / len(vendor_scores) if vendor_scores else None
            )

            buy_score = avg_vendor_score

            # Use buy_build_analysis score when no vendor scores available
            # options is a list [{course_of_action, scores: {weighted_total}}, ...]
            _llm_options = buy_build_analysis.get("options") or []
            bb_buy_score = next(
                (
                    (opt.get("scores") or {}).get("weighted_total", 0)
                    for opt in (_llm_options if isinstance(_llm_options, list) else [])
                    if str(opt.get("course_of_action", "")).lower() == "buy"
                ),
                0,
            ) or 0
            if buy_score is None and bb_buy_score:
                buy_score = bb_buy_score  # LLM already returns 0–100
            elif buy_score is not None and bb_buy_score:
                buy_score = (buy_score + bb_buy_score) / 2
            if buy_score is None:
                buy_score = 0  # No data — show low score rather than fabricate

            # Cap at 90 so scores can be differentiated from a perfect BUILD
            # Boost for compliance — certified SaaS vendors typically pre-meet requirements
            buy_score = min(90, buy_score + (_compliance_count * 5))

            # Aggregate estimated costs from vendor options when available
            buy_estimated_cost = None
            vendor_costs = [v.get("estimated_cost") for v in vendors if v.get("estimated_cost") is not None]
            if vendor_costs:
                buy_estimated_cost = min(vendor_costs)  # Best-case from options

            buy_timeline = max(3, round(_base_timeline * 0.5))

            buy_pros = [
                "Proven, mature solutions with established roadmap",
                "Vendor provides ongoing support and updates",
                f"Faster deployment vs build (~{buy_timeline} months)",
                "Shared development costs across customer base",
            ] + _compliance_pros("BUY")

            buy_cons = [
                "Vendor lock-in — switching costs escalate over time",
                "Ongoing subscription/licence costs",
                "Limited customisation to exact requirements",
            ] + _constraints_cons("BUY")

            recommendations.append(
                {
                    "option_type": "BUY",
                    "score": round(buy_score, 1),
                    "estimated_cost": buy_estimated_cost,
                    "timeline_months": buy_timeline,
                    "risk_level": "MEDIUM",
                    "description": f"Purchase commercial solution from {len(vendors)} evaluated vendor(s)",
                    "pros": buy_pros,
                    "cons": buy_cons,
                    "details": {"top_vendors": vendors[:3], "total_options_found": len(vendors)},
                }
            )

        # Option 3: BUILD
        # options is a list [{course_of_action, scores: {weighted_total}}, ...]
        _llm_options2 = buy_build_analysis.get("options") or []
        bb_build_score = next(
            (
                (opt.get("scores") or {}).get("weighted_total", 0)
                for opt in (_llm_options2 if isinstance(_llm_options2, list) else [])
                if str(opt.get("course_of_action", "")).lower() == "build"
            ),
            0,
        ) or 0
        build_score = bb_build_score if bb_build_score else 50  # LLM already returns 0–100

        # Adjust based on complexity
        if context.get("is_critical"):
            build_score -= 10  # Higher risk for critical systems
        # Compliance penalty — custom builds require certification effort per requirement
        if _compliance_count:
            build_score -= _compliance_count * 10

        # Estimate BUILD timeline: ~120% of stated timeline (custom build overruns are common)
        build_timeline = max(6, round(_base_timeline * 1.2))

        # Estimate BUILD cost from team size and timeline (3 devs @ £600/day)
        build_cost_estimate = None
        if _base_timeline:
            working_days = build_timeline * 20  # ~20 working days/month
            build_cost_estimate = round(3 * 600 * working_days, -3)  # 3 devs × £600/day, round to £k

        build_pros = [
            "Complete control over architecture and roadmap",
            "Tailored precisely to requirements — no compromise",
            "No vendor lock-in or licence dependency",
            "IP ownership retained in-house",
        ] + _compliance_pros("BUILD")

        build_cons = [
            f"Longest time to market (~{build_timeline} months estimated)",
            "Highest upfront investment — team, infra, testing",
            "Ongoing maintenance burden on internal teams",
            "Resource intensive — risks competing with BAU priorities",
            "Technical debt accumulation without governance",
        ] + _constraints_cons("BUILD")

        recommendations.append(
            {
                "option_type": "BUILD",
                "score": round(min(100, build_score), 1),
                "estimated_cost": build_cost_estimate,
                "timeline_months": build_timeline,
                "risk_level": "HIGH",
                "description": f"Custom development — {build_timeline} months, est. £{build_cost_estimate:,.0f}" if build_cost_estimate else "Custom development solution",
                "pros": build_pros,
                "cons": build_cons,
                "details": {
                    "team_size": "3 - 5 developers",
                    "tech_stack": "Modern cloud-native",
                    "maintenance_cost_annual": round(build_cost_estimate * 0.2, -3) if build_cost_estimate else None,
                },
            }
        )

        # Option 4: HYBRID (if both reuse and buy are viable)
        if existing_apps.get("found_count", 0) > 0 and vendor_options.get("found_count", 0) > 0:
            # Use option_type lookup (not list index) to avoid order dependency
            reuse_score_val = next((r["score"] for r in recommendations if r.get("option_type") == "REUSE"), 0)
            buy_score_val = next((r["score"] for r in recommendations if r.get("option_type") == "BUY"), 0)
            hybrid_score = (reuse_score_val + buy_score_val) / 2 + 5  # Slight bonus

            # Hybrid cost: sum of reuse enhancement + lowest vendor option when available
            hybrid_cost = None
            reuse_cost = next(
                (r.get("estimated_cost") for r in recommendations if r.get("option_type") == "REUSE"),
                None,
            )
            buy_cost = next(
                (r.get("estimated_cost") for r in recommendations if r.get("option_type") == "BUY"),
                None,
            )
            if reuse_cost is not None and buy_cost is not None:
                hybrid_cost = reuse_cost + buy_cost
            elif buy_cost is not None:
                hybrid_cost = buy_cost

            hybrid_timeline = max(3, round(_base_timeline * 0.65))

            hybrid_pros = [
                "Best of both worlds — extend existing + fill gaps with COTS",
                "Phased implementation reduces delivery risk",
                "Lower initial cost than full replacement",
                "Retains existing institutional knowledge",
            ] + _compliance_pros("BUY")[:1]

            recommendations.append(
                {
                    "option_type": "HYBRID",
                    "score": round(hybrid_score, 1),
                    "estimated_cost": hybrid_cost,
                    "timeline_months": hybrid_timeline,
                    "risk_level": "MEDIUM",
                    "description": f"Extend existing apps + buy for gaps (~{hybrid_timeline} months)",
                    "pros": hybrid_pros,
                    "cons": [
                        "Integration complexity between old and new systems",
                        "Multiple systems to operate and maintain",
                        "Coordination overhead across vendors and teams",
                    ] + _constraints_cons("BUY")[:1],
                    "details": {
                        "approach": "Reuse for core functions, buy for gaps",
                        "integration_points": "2-3",
                    },
                }
            )

        # Sort by score descending
        recommendations.sort(key=lambda x: x["score"], reverse=True)

        return recommendations
