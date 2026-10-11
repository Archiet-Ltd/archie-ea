"""
Autonomous Architecture Assistant Service

Provides AI-driven solution design orchestration, vendor analysis integration,
option generation with pros/cons, and ARB submission draft generation for
Enterprise Architects.

Features:
- AI-driven solution design orchestration
- Intent handlers for "design solution for capability X"
- Vendor analysis integration for option generation
- ARB submission draft generator
- Solution comparison with pros/cons
- Cost/benefit analysis

Usage:
    service = ArchitectureAssistantService()

    # Design a solution for a capability
    result = service.design_solution(capability_id=123, requirements={...})

    # Generate vendor options
    options = service.generate_vendor_options(capability_id=123)

    # Compare options
    comparison = service.compare_options(option_ids=[1, 2, 3])

    # Generate ARB draft
    draft = service.generate_arb_draft(option_id=1)
"""

from app.services.archimate_backbone import sync_archimate_element
import hashlib
import json
import logging
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import current_app

from .. import db
from ..models.ai_recommendations import AIRecommendation
from ..services.archimate_model_generator import ArchiMateModelGenerator
from ..services.recommendations_engine_service import RecommendationsEngineService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ENH-010: Capability and application catalog cache (TTL-based, in-process)
# ---------------------------------------------------------------------------
_capability_cache: dict = {}
_CAPABILITY_CACHE_TTL = 300  # 5 minutes


def _invalidate_capability_cache():
    """Clear capability caches.  Call after UnifiedCapability create/update/delete."""
    _capability_cache.clear()


def _get_capability_by_id(capability_id: int):
    """Return a UnifiedCapability by primary key, using an in-process TTL cache.

    The first call within the TTL window fetches the full capability list and
    indexes it by id and by name (lower-cased).  Subsequent calls hit the cache.
    """
    _ensure_capability_cache()
    return _capability_cache.get("by_id", {}).get(int(capability_id))


def _get_capability_by_name(name: str):
    """Return a UnifiedCapability whose name matches (case-insensitive)."""
    _ensure_capability_cache()
    return _capability_cache.get("by_name", {}).get((name or "").strip().lower())


def _ensure_capability_cache():
    """Populate the cache if empty or expired."""
    now = _time.time()
    if _capability_cache.get("ts") and now - _capability_cache["ts"] < _CAPABILITY_CACHE_TTL:
        return  # still fresh

    try:
        from ..models.unified_capability import UnifiedCapability

        caps = UnifiedCapability.query.all()
        by_id = {c.id: c for c in caps}
        by_name = {c.name.strip().lower(): c for c in caps if c.name}
        _capability_cache["by_id"] = by_id
        _capability_cache["by_name"] = by_name
        _capability_cache["ts"] = now
        logger.debug("ENH-010: Capability cache refreshed — %d entries", len(caps))
    except Exception as exc:
        logger.warning("ENH-010: Could not refresh capability cache: %s", exc)


# =============================================================================
# Data Classes for Solution Design
# =============================================================================


@dataclass
class SolutionOption:
    """Represents a solution option with pros/cons."""

    id: str
    name: str
    vendor_name: Optional[str] = None
    vendor_id: Optional[int] = None
    option_type: str = "vendor"  # vendor, build, hybrid, existing
    description: Optional[str] = None
    total_score: float = 0.0
    cost_estimate: Optional[float] = None
    implementation_weeks: Optional[int] = None
    capability_coverage: float = 0.0
    pros: List[str] = field(default_factory=list)
    cons: List[str] = field(default_factory=list)
    risk_factors: List[Dict[str, Any]] = field(default_factory=list)
    strategic_fit_score: float = 0.0
    recommendation_rank: int = 0
    # Governance alignment against retrieved architecture context. Both stay at
    # their defaults when no context is supplied, so ranking is unchanged.
    governance_alignment: float = 0.0
    governance_notes: List[str] = field(default_factory=list)


@dataclass
class ARBSubmissionDraft:
    """Draft ARB submission with pre-filled content."""

    title: str
    description: str
    review_type: str
    togaf_phase: str
    business_justification: str
    technical_assessment: str
    risk_analysis: str
    implementation_approach: str
    cost_estimates: Dict[str, Any]
    capability_impacts: List[Dict[str, Any]]
    recommended_option: Optional[SolutionOption] = None
    alternative_options: List[SolutionOption] = field(default_factory=list)


@dataclass
class GapAnalysisResult:
    """Result of capability gap analysis."""

    capability_id: int
    capability_name: str
    current_coverage: float
    target_coverage: float
    gap_severity: str
    gap_description: str
    recommended_solutions: List[SolutionOption]
    estimated_investment: Optional[float] = None


class ArchitectureAssistantService:
    """
    AI-driven architecture assistant service.

    Orchestrates solution design, vendor analysis, option generation,
    and ARB submission drafting for Enterprise Architects.
    """

    # Intent patterns for natural language processing
    INTENT_PATTERNS = {
        "design_solution": [
            r"design\s+(?:a\s+)?solution\s+for\s+(?:capability\s+)?(.+)",
            r"create\s+(?:a\s+)?solution\s+for\s+(.+)",
            r"need\s+(?:a\s+)?solution\s+for\s+(.+)",
            r"how\s+(?:can\s+we|to)\s+(?:solve|address)\s+(.+)",
        ],
        "compare_vendors": [
            r"compare\s+(?:vendors?\s+)?for\s+(.+)",
            r"vendor\s+comparison\s+for\s+(.+)",
            r"which\s+vendor\s+(?:for|should)\s+(.+)",
        ],
        "generate_arb": [
            r"generate\s+arb\s+(?:submission|draft)\s+for\s+(.+)",
            r"create\s+arb\s+(?:submission|draft)\s+for\s+(.+)",
            r"draft\s+arb\s+(?:submission)?\s+for\s+(.+)",
        ],
        "analyze_gap": [
            r"analyze\s+(?:capability\s+)?gap\s+(?:for|in)\s+(.+)",
            r"gap\s+analysis\s+for\s+(.+)",
            r"find\s+gaps\s+in\s+(.+)",
        ],
    }

    # Default weights for option scoring
    DEFAULT_SCORING_WEIGHTS = {
        "cost": 0.25,
        "capability_coverage": 0.25,
        "risk": 0.20,
        "strategic_fit": 0.15,
        "implementation": 0.15,
    }

    def __init__(self):
        """Initialize the Architecture Assistant Service."""
        self.app = current_app._get_current_object() if current_app else None

    # =========================================================================
    # Solution Design Methods
    # =========================================================================

    def design_solution(
        self,
        capability_id: int,
        requirements: Optional[Dict[str, Any]] = None,
        constraints: Optional[Dict[str, Any]] = None,
        include_vendor_analysis: bool = True,
        rag_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Design a solution for a specific capability.

        Args:
            capability_id: ID of the target capability
            requirements: Optional requirements dictionary
            constraints: Optional constraints (budget, timeline, etc.)
            include_vendor_analysis: Include vendor options analysis
            rag_context: Retrieved architecture context (principles, prior ARB
                decisions, reference architectures, established patterns) as
                returned by ArchitectureRAGService.get_context_for_solution().
                Optional — omitted, ranking is unchanged.

        Returns:
            Dictionary with solution design including options and recommendations
        """
        logger.info(f"Designing solution for capability {capability_id}")

        try:
            capability = _get_capability_by_id(capability_id)  # ENH-010: cached lookup
            if not capability:
                return {"error": f"Capability {capability_id} not found"}

            # Get capability context
            context = self._get_capability_context(capability)

            # Generate solution options
            options = []
            if include_vendor_analysis:
                vendor_options = self.generate_vendor_options(
                    capability_id=capability_id, constraints=constraints
                )
                options.extend(vendor_options.get("options", []))

            # Add build option
            build_option = self._generate_build_option(capability, requirements, constraints)
            options.append(build_option)

            # Add hybrid option if applicable
            if len(options) >= 2:
                hybrid_option = self._generate_hybrid_option(capability, options[:2], constraints)
                options.append(hybrid_option)

            # Rank options
            ranked_options = self._rank_options(options, constraints, rag_context)

            # Generate recommendation
            recommendation = self._generate_recommendation(capability, ranked_options)

            return {
                "capability_id": capability_id,
                "capability_name": capability.name,
                "context": context,
                "options": [
                    asdict(opt) if hasattr(opt, "__dataclass_fields__") else opt
                    for opt in ranked_options
                ],
                "recommendation": recommendation,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error designing solution: {e}")
            return {"error": str(e)}

    def generate_vendor_options(
        self, capability_id: int, constraints: Optional[Dict[str, Any]] = None, max_options: int = 5
    ) -> Dict[str, Any]:
        """
        Generate vendor options for a capability.

        Args:
            capability_id: ID of the target capability
            constraints: Optional constraints (budget, timeline, etc.)
            max_options: Maximum number of options to generate

        Returns:
            Dictionary with vendor options and analysis
        """
        logger.info(f"Generating vendor options for capability {capability_id}")

        try:
            from ..models.vendor_analysis import OptionsAnalysis

            capability = _get_capability_by_id(capability_id)  # ENH-010: cached lookup
            if not capability:
                return {"error": f"Capability {capability_id} not found"}

            options = []

            # Check for existing analysis
            existing_analysis = (
                OptionsAnalysis.query.filter_by(capability_id=capability_id, status="completed")
                .order_by(OptionsAnalysis.completed_at.desc())
                .first()
            )

            if existing_analysis:
                # Use existing vendor options
                for vo in existing_analysis.vendor_options[:max_options]:
                    option = self._convert_vendor_option(vo)
                    options.append(option)
            else:
                # Search for relevant vendor products
                products = self._search_vendor_products(capability, constraints)

                for product in products[:max_options]:
                    option = self._create_vendor_option_from_product(product, capability)
                    options.append(option)

            # Score and rank options
            scored_options = self._score_vendor_options(options, capability, constraints)

            return {
                "capability_id": capability_id,
                "capability_name": capability.name,
                "options": [asdict(opt) for opt in scored_options],
                "analysis_source": "existing" if existing_analysis else "generated",
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error generating vendor options: {e}")
            return {"error": str(e)}

    def compare_options(
        self,
        option_ids: List[int] = None,
        options_data: List[Dict[str, Any]] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Compare solution options with detailed analysis.

        Args:
            option_ids: List of VendorOption IDs to compare (from database)
            options_data: List of option dictionaries (for custom options)
            weights: Custom scoring weights

        Returns:
            Dictionary with comparison matrix and analysis
        """
        logger.info("Comparing solution options")

        try:
            from ..models.vendor_analysis import OptionsAnalysis, VendorOption

            weights = weights or self.DEFAULT_SCORING_WEIGHTS
            options = []

            # Load options from database if IDs provided. option_ids comes from the request body,
            # and VendorOption has no organisation column of its own: an id outside the caller's
            # organisation is skipped rather than resolved, the same fence as vendor_catalog_routes.
            if option_ids:
                for opt_id in option_ids:
                    # tenant-scoping-ok: the fenced select two lines below is the tenant check.
                    # Not db.session.get() -- it bypasses the ORM tenant filter on an identity-map hit.
                    vo = db.session.get(VendorOption, opt_id)
                    fenced_analysis = db.session.execute(
                        db.select(OptionsAnalysis).where(OptionsAnalysis.id == vo.analysis_id)
                    ).scalar_one_or_none() if vo and vo.analysis_id else None
                    if vo and fenced_analysis:
                        option = self._convert_vendor_option(vo)
                        options.append(option)

            # Convert options data if provided
            if options_data:
                for opt_data in options_data:
                    option = SolutionOption(
                        id=opt_data.get("id", str(len(options))),
                        name=opt_data.get("name", "Unknown"),
                        vendor_name=opt_data.get("vendor_name"),
                        description=opt_data.get("description"),
                        cost_estimate=opt_data.get("cost_estimate"),
                        capability_coverage=opt_data.get("capability_coverage", 0),
                        pros=opt_data.get("pros", []),
                        cons=opt_data.get("cons", []),
                    )
                    options.append(option)

            if not options:
                return {"error": "No options provided for comparison"}

            # Score options
            for option in options:
                option.total_score = self._calculate_option_score(option, weights)

            # Sort by score
            options.sort(key=lambda x: x.total_score, reverse=True)

            # Assign ranks
            for idx, option in enumerate(options, 1):
                option.recommendation_rank = idx

            # Generate comparison matrix
            comparison_matrix = self._generate_comparison_matrix(options)

            # Generate insights
            insights = self._generate_comparison_insights(options)

            return {
                "options": [asdict(opt) for opt in options],
                "comparison_matrix": comparison_matrix,
                "insights": insights,
                "weights_used": weights,
                "winner": asdict(options[0]) if options else None,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error comparing options: {e}")
            return {"error": str(e)}

    def analyze_options(
        self,
        option_ids: Optional[List[int]] = None,
        options_data: Optional[List[Dict[str, Any]]] = None,
        capability_id: Optional[int] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze multiple solution options, enrich each with ArchiMate suggestions and simple governance checklist,
        and return a decision rationale and enriched comparison results.
        """
        logger.info("Analyzing options")
        try:
            if not option_ids and not options_data:
                return {"error": "Either option_ids or options_data must be provided"}

            # Reuse existing comparison logic to score and rank options
            compare_result = self.compare_options(
                option_ids=option_ids, options_data=options_data, weights=weights
            )
            if "error" in compare_result:
                return {"error": compare_result["error"]}

            options = compare_result.get("options", [])

            # Use SolutionAIService to generate ArchiMate element suggestions per option
            try:
                from .solution_ai_service import SolutionAIService

                ai_service = SolutionAIService()
            except Exception:
                ai_service = None

            enriched_options = []

            for opt in options:
                # Ensure we have a mutable dict
                opt_copy = dict(opt) if isinstance(opt, dict) else asdict(opt)

                # Build a short description for AI
                desc = (
                    opt_copy.get("description")
                    or f"{opt_copy.get('name', '')} {opt_copy.get('vendor_name', '')}"
                )

                # Optionally include capability context
                capability_context = None
                if capability_id:
                    try:
                        cap = _get_capability_by_id(capability_id)  # ENH-010: cached lookup
                        if cap:
                            capability_context = {
                                "id": cap.id,
                                "name": cap.name,
                                "level": getattr(cap, "level", None),
                                "strategic_importance": getattr(cap, "strategic_importance", None),
                            }
                    except Exception:
                        capability_context = None

                # Get ArchiMate suggestions from AI service (best-effort)
                suggestions = {}
                if ai_service:
                    try:
                        ai_res = ai_service.suggest_elements(
                            solution_description=desc,
                            capabilities=[capability_context] if capability_context else None,
                        )
                        if isinstance(ai_res, dict) and ai_res.get("success"):
                            suggestions = ai_res.get("suggestions") or {}
                    except Exception as e:
                        logger.warning(f"AI suggestion failed for option {opt_copy.get('id')}: {e}")

                opt_copy["archimate_suggestions"] = suggestions

                # Simple governance checklist
                checklist = []
                checklist.append(
                    {"check": "cost_estimate_present", "pass": bool(opt_copy.get("cost_estimate"))}
                )
                checklist.append(
                    {
                        "check": "capability_coverage",
                        "value": opt_copy.get("capability_coverage", 0),
                    }
                )
                checklist.append(
                    {"check": "has_risks", "value": len(opt_copy.get("risk_factors") or []) > 0}
                )
                opt_copy["governance_checklist"] = checklist

                enriched_options.append(opt_copy)

            # Decision rationale based on scores
            winner = compare_result.get("winner")
            rationale = ""
            if winner:
                try:
                    rationale = f"Selected '{winner.get('name')}' with score {winner.get('total_score')} as the best fit based on configured weights and scoring."
                except Exception:
                    rationale = f"Selected '{winner.get('name')}' as the recommended option."

            return {
                "options": enriched_options,
                "comparison_matrix": compare_result.get("comparison_matrix"),
                "insights": compare_result.get("insights"),
                "winner": winner,
                "decision_rationale": rationale,
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Error analyzing options: {e}")
            return {"error": str(e)}

    # =========================================================================
    # ARB Draft Generation Methods
    # =========================================================================

    def generate_arb_draft(
        self,
        capability_id: int,
        recommended_option: Optional[Dict[str, Any]] = None,
        alternative_options: Optional[List[Dict[str, Any]]] = None,
        additional_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate an ARB submission draft with pre-filled content.

        Args:
            capability_id: ID of the target capability
            recommended_option: Dictionary with recommended solution option
            alternative_options: List of alternative option dictionaries
            additional_context: Additional context for the submission

        Returns:
            Dictionary with ARB submission draft
        """
        logger.info(f"Generating ARB draft for capability {capability_id}")

        try:
            from ..models.architecture_review_board import ReviewType, TOGAFPhase

            capability = _get_capability_by_id(capability_id)  # ENH-010: cached lookup
            if not capability:
                return {"error": f"Capability {capability_id} not found"}

            # Generate draft content
            draft = ARBSubmissionDraft(
                title=self._generate_arb_title(capability, recommended_option),
                description=self._generate_arb_description(capability, recommended_option),
                review_type=ReviewType.CAPABILITY_IMPLEMENTATION.value,
                togaf_phase=TOGAFPhase.PHASE_E.value,
                business_justification=self._generate_business_justification(
                    capability, recommended_option, additional_context
                ),
                technical_assessment=self._generate_technical_assessment(
                    capability, recommended_option, alternative_options
                ),
                risk_analysis=self._generate_risk_analysis(recommended_option, alternative_options),
                implementation_approach=self._generate_implementation_approach(
                    capability, recommended_option
                ),
                cost_estimates=self._generate_cost_estimates(
                    recommended_option, alternative_options
                ),
                capability_impacts=self._generate_capability_impacts(capability),
            )

            # Convert recommended option
            if recommended_option:
                draft.recommended_option = SolutionOption(
                    id=str(recommended_option.get("id", "1")),
                    name=recommended_option.get("name", "Recommended Solution"),
                    vendor_name=recommended_option.get("vendor_name"),
                    description=recommended_option.get("description"),
                    cost_estimate=recommended_option.get("cost_estimate"),
                    pros=recommended_option.get("pros", []),
                    cons=recommended_option.get("cons", []),
                )

            # Convert alternative options
            if alternative_options:
                for alt in alternative_options:
                    alt_option = SolutionOption(
                        id=str(alt.get("id", "")),
                        name=alt.get("name", "Alternative"),
                        vendor_name=alt.get("vendor_name"),
                        description=alt.get("description"),
                        cost_estimate=alt.get("cost_estimate"),
                        pros=alt.get("pros", []),
                        cons=alt.get("cons", []),
                    )
                    draft.alternative_options.append(alt_option)

            # Convert to dictionary
            draft_dict = asdict(draft)

            # Derive display-friendly cost_summary and timeline for the ARB header
            cost_est = draft_dict.get('cost_estimates') or {}
            cost_total = (cost_est.get('summary') or {}).get('total_estimated')
            draft_dict['cost_summary'] = f"${int(cost_total):,}" if cost_total else 'To be determined'
            draft_dict['timeline'] = (
                str(recommended_option.get('implementation_weeks')) + ' weeks'
                if (recommended_option and recommended_option.get('implementation_weeks'))
                else 'To be determined'
            )

            # Add metadata
            draft_dict["capability_id"] = capability_id
            draft_dict["capability_name"] = capability.name
            draft_dict["generated_at"] = datetime.utcnow().isoformat()
            draft_dict["version"] = "1.0"

            # Include ArchiMate viewpoints if provided by recommended option analysis
            if recommended_option and isinstance(recommended_option, dict):
                draft_dict["archimate_viewpoints"] = recommended_option.get(
                    "archimate_suggestions", {}
                )
            else:
                draft_dict["archimate_viewpoints"] = {}

            # Decision rationale (from option analysis) if present
            if additional_context and isinstance(additional_context, dict):
                draft_dict["decision_rationale"] = additional_context.get("decision_rationale")
            else:
                draft_dict["decision_rationale"] = None

            return {
                "draft": draft_dict,
                "message": "ARB submission draft generated successfully",
                "next_steps": [
                    "Review and refine business justification",
                    "Validate cost estimates with finance team",
                    "Add detailed implementation timeline",
                    "Obtain stakeholder sign-off",
                    "Submit to ARB for review",
                ],
            }

        except Exception as e:
            logger.error(f"Error generating ARB draft: {e}")
            return {"error": str(e)}

    def generate_archimate_model(
        self,
        capability_id: int,
        solution_options: List[Dict[str, Any]],
        gap_analysis: Dict[str, Any],
        include_viewpoints: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate complete ArchiMate 3.2 model from capability analysis.

        Args:
            capability_id: ID of the analyzed capability
            solution_options: List of solution options with vendor/product details
            gap_analysis: Gap analysis results
            include_viewpoints: Whether to include generated viewpoints

        Returns:
            Complete ArchiMate model with elements, relationships, and viewpoints
        """
        logger.info(f"Generating ArchiMate model for capability {capability_id}")

        try:
            # Use the ArchiMateModelGenerator service
            generator = ArchiMateModelGenerator()
            model = generator.generate_model_from_capability_analysis(
                capability_id=capability_id,
                solution_options=solution_options,
                gap_analysis=gap_analysis,
            )

            if "error" in model:
                return model

            # Optionally filter viewpoints if not requested
            if not include_viewpoints and "viewpoints" in model:
                model["viewpoints"] = []

            return model

        except Exception as e:
            logger.error(f"Error generating ArchiMate model: {e}")
            return {"error": str(e)}

    def get_recommendations(
        self, capability_id: int, include_analysis: bool = True
    ) -> Dict[str, Any]:
        """
        Get AI-generated recommendations for a capability.

        Args:
            capability_id: ID of the target capability
            include_analysis: Include detailed analysis

        Returns:
            Dictionary with recommendations and analysis
        """
        logger.info(f"Getting recommendations for capability {capability_id}")

        try:
            capability = _get_capability_by_id(capability_id)  # ENH-010: cached lookup
            if not capability:
                return {"error": f"Capability {capability_id} not found"}

            # Get capability context
            context = self._get_capability_context(capability)

            # Generate recommendations based on context
            recommendations = []

            # Check coverage
            if context.get("current_coverage", 0) < 50:
                recommendations.append(
                    {
                        "priority": "high",
                        "category": "coverage",
                        "title": "Address Low Coverage",
                        "description": f"Capability '{capability.name}' has only {context.get('current_coverage', 0)}% coverage. Consider investing in solutions to improve this.",
                        "action": "Initiate vendor analysis and solution design",
                    }
                )

            # Check for multiple applications
            if context.get("application_count", 0) > 3:
                recommendations.append(
                    {
                        "priority": "medium",
                        "category": "rationalization",
                        "title": "Rationalization Opportunity",
                        "description": f"Multiple applications ({context.get('application_count')}) support this capability. Consider consolidation.",
                        "action": "Evaluate application portfolio for redundancy",
                    }
                )

            # Check strategic importance
            if capability.strategic_importance == "critical":
                recommendations.append(
                    {
                        "priority": "high",
                        "category": "strategic",
                        "title": "Critical Capability Attention",
                        "description": "This is a strategically critical capability. Ensure robust solution architecture.",
                        "action": "Schedule architecture review and ensure proper governance",
                    }
                )

            # Add vendor options if low coverage
            vendor_options = []
            if context.get("current_coverage", 0) < 75 and include_analysis:
                options_result = self.generate_vendor_options(capability_id, max_options=3)
                vendor_options = options_result.get("options", [])

            return {
                "capability_id": capability_id,
                "capability_name": capability.name,
                "context": context,
                "recommendations": recommendations,
                "vendor_options": vendor_options,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error getting recommendations: {e}")
            return {"error": str(e)}

    def analyze_gap(
        self,
        capability_ids: List[int],
        target_coverage: float = 100.0,
        include_solutions: bool = True,
    ) -> Dict[str, Any]:
        """
        Analyze one or more capability gaps and suggest combined solutions.

        Args:
            capability_ids: List of capability IDs to analyze
            target_coverage: Target coverage percentage
            include_solutions: Include solution suggestions

        Returns:
            Dictionary with combined gap analysis and solutions
        """
        logger.info(f"Analyzing gap for capabilities {capability_ids}")

        try:
            from ..models.unified_application_capability_mapping import (
                UnifiedApplicationCapabilityMapping,
            )

            # Normalize input to list
            if not isinstance(capability_ids, list):
                capability_ids = [capability_ids]

            capabilities = []
            for cid in capability_ids:
                cap = _get_capability_by_id(cid)  # ENH-010: cached lookup
                if not cap:
                    return {"error": f"Capability {cid} not found"}
                capabilities.append(cap)

            # Batch-load all mappings for the requested capabilities to avoid N+1 queries
            capability_id_list = [cap.id for cap in capabilities]
            all_mappings = UnifiedApplicationCapabilityMapping.query.filter(
                UnifiedApplicationCapabilityMapping.unified_capability_id.in_(capability_id_list),
                UnifiedApplicationCapabilityMapping.is_active == True,  # noqa: E712
            ).all()
            mappings_by_cap = {}
            for m in all_mappings:
                mappings_by_cap.setdefault(m.unified_capability_id, []).append(m)

            # Calculate coverage per capability
            breakdown = []
            coverage_values = []
            total_estimated_investment = 0

            for capability in capabilities:
                mappings = mappings_by_cap.get(capability.id, [])

                total_coverage = 0
                app_count = 0
                for mapping in mappings:
                    if mapping.coverage_percentage:
                        total_coverage += mapping.coverage_percentage
                        app_count += 1

                current_coverage = total_coverage / app_count if app_count > 0 else 0
                coverage_values.append(current_coverage)
                gap_amount = target_coverage - current_coverage

                gap_severity = self._calculate_gap_severity(
                    gap_amount, capability.strategic_importance, capability.business_criticality
                )

                # Suggest solutions for this capability if needed
                solutions = []
                if include_solutions and gap_amount > 0:
                    design_result = self.design_solution(
                        capability_id=capability.id, requirements={"min_coverage": target_coverage}
                    )
                    if "options" in design_result:
                        for opt in design_result["options"][:3]:
                            solutions.append(
                                SolutionOption(**opt) if isinstance(opt, dict) else opt
                            )

                est_invest = self._estimate_gap_investment(gap_amount, capability)
                total_estimated_investment += est_invest

                breakdown.append(
                    {
                        "capability_id": capability.id,
                        "capability_name": capability.name,
                        "current_coverage": round(current_coverage, 2),
                        "target_coverage": target_coverage,
                        "gap": round(gap_amount, 2),
                        "gap_severity": gap_severity,
                        "recommended_solutions": [asdict(s) for s in solutions],
                        "estimated_investment": est_invest,
                    }
                )

            avg_current_coverage = (
                round(sum(coverage_values) / len(coverage_values), 2) if coverage_values else 0
            )
            combined_gap = target_coverage - avg_current_coverage

            # Combined severity pick highest severity among breakdown
            severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
            combined_severity = (
                max([b["gap_severity"] for b in breakdown], key=lambda s: severity_rank.get(s, 0))
                if breakdown
                else "none"
            )

            # Aggregate recommended solutions (deduplicated by name)
            all_recommendations = []
            for b in breakdown:
                all_recommendations.extend(b.get("recommended_solutions", []))

            seen = set()
            unique_recommendations = []
            for rec in all_recommendations:
                name = rec.get("name") if isinstance(rec, dict) else getattr(rec, "name", None)
                if name and name not in seen:
                    seen.add(name)
                    unique_recommendations.append(rec)

            # Limit recommendations
            unique_recommendations = unique_recommendations[:6]

            # Integrate with RecommendationsEngine for portfolio-wide insights
            portfolio_insights = None
            try:
                rec_engine = RecommendationsEngineService()
                recommendations = rec_engine.get_all_recommendations(
                    persona="enterprise_architect", refresh=False
                )

                # Filter recommendations relevant to selected capabilities
                relevant_alerts = [
                    alert
                    for alert in recommendations.get("alerts", [])
                    if alert.get("type") in ["cross_domain", "capability"]
                ]

                relevant_recommendations = [
                    rec
                    for rec in recommendations.get("recommendations", [])
                    if rec.get("type") in ["capability", "cross_domain"]
                ]

                portfolio_insights = {
                    "health_score": recommendations.get("health_score", 0),
                    "relevant_alerts": relevant_alerts[:5],  # Top 5 alerts
                    "relevant_recommendations": relevant_recommendations[
                        :3
                    ],  # Top 3 recommendations
                    "summary": recommendations.get("summary", {}),
                }

                logger.info(
                    f"Integrated portfolio insights with {len(relevant_alerts)} alerts and {len(relevant_recommendations)} recommendations"
                )
            except Exception as e:
                logger.warning(f"Could not integrate portfolio insights: {e}")
                portfolio_insights = None

            # AWIZ-010: persist Gap and Plateau ArchiMate elements
            gap_elements = []
            try:
                gap_elements = self._create_gap_plateau_elements(
                    capabilities, breakdown, target_coverage
                )
            except Exception as ge:
                logger.warning(f"Gap/Plateau element creation failed (non-blocking): {ge}")

            return {
                "capability_ids": [c.id for c in capabilities],
                "capability_names": [c.name for c in capabilities],
                "current_coverage": avg_current_coverage,
                "target_coverage": target_coverage,
                "combined_gap": combined_gap,
                "gap_severity": combined_severity,
                "breakdown": breakdown,
                "recommended_solutions": [
                    asdict(r) if hasattr(r, "__dataclass_fields__") else r
                    for r in unique_recommendations
                ],
                "estimated_investment": total_estimated_investment,
                "portfolio_insights": portfolio_insights,
                "gap_elements": gap_elements,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error analyzing gap: {e}")
            return {"error": str(e)}

    def _create_gap_plateau_elements(
        self, capabilities: list, breakdown: list, target_coverage: float
    ) -> list:
        """AWIZ-010: Persist ArchiMate Gap and Plateau elements for each capability below target."""
        from ..models.implementation_migration import Gap, Plateau

        result = []
        for cap, item in zip(capabilities, breakdown):
            current = item.get("current_coverage", 0)
            if current >= target_coverage:
                continue

            baseline = Plateau(
                name=f"Baseline: {cap.name}",
                description=f"Current coverage: {round(current, 1)}%",
            )
            target_p = Plateau(
                name=f"Target: {cap.name}",
                description=f"Target coverage: {round(target_coverage, 1)}%",
            )
            db.session.add(baseline)
            sync_archimate_element(baseline)
            db.session.add(target_p)
            sync_archimate_element(target_p)
            db.session.flush()

            gap = Gap(
                name=f"Gap: {cap.name}",
                description=f"Coverage gap of {round(target_coverage - current, 1)}%",
                originating_plateau_id=baseline.id,
                target_plateau_id=target_p.id,
            )
            db.session.add(gap)
            db.session.flush()

            result.append({
                "gap_id": gap.id,
                "from_plateau_id": baseline.id,
                "to_plateau_id": target_p.id,
                "name": gap.name,
            })

        if result:
            db.session.commit()
        return result

    # =========================================================================
    # Helper Methods - Capability Context
    # =========================================================================

    def _get_capability_context(self, capability) -> Dict[str, Any]:
        """Get context information for a capability."""
        from ..models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )

        mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
            unified_capability_id=capability.id, is_active=True
        ).all()

        total_coverage = 0
        app_count = 0

        for mapping in mappings:
            if mapping.coverage_percentage:
                total_coverage += mapping.coverage_percentage
                app_count += 1

        avg_coverage = total_coverage / app_count if app_count > 0 else 0

        return {
            "current_coverage": round(avg_coverage, 2),
            "application_count": app_count,
            "strategic_importance": capability.strategic_importance,
            "business_criticality": capability.business_criticality,
            "level": capability.level,
            "domain_id": capability.domain_id,
        }

    def _search_vendor_products(self, capability, constraints: Optional[Dict] = None) -> List:
        """Search for relevant vendor products for a capability."""
        try:
            from ..models.vendor.vendor_organization import VendorProduct

            # Basic search - can be enhanced with AI matching
            products = VendorProduct.query.limit(10).all()

            # Apply constraints if provided
            if constraints:
                budget = constraints.get("budget")
                if budget:
                    # Filter by estimated cost if available
                    pass

            return products
        except Exception as e:
            logger.warning(f"Error searching vendor products: {e}")
            return []

    def _convert_vendor_option(self, vendor_option) -> SolutionOption:
        """Convert a VendorOption model to SolutionOption dataclass."""
        pros, cons = vendor_option.get_pros_cons()

        return SolutionOption(
            id=str(vendor_option.id),
            name=vendor_option.vendor_name or "Unknown Vendor",
            vendor_name=vendor_option.vendor_name,
            vendor_id=vendor_option.technology_stack_id,
            option_type="vendor",
            description=vendor_option.analyst_notes,
            total_score=vendor_option.total_score or 0.0,
            cost_estimate=float(vendor_option.tco_total) if vendor_option.tco_total else None,
            implementation_weeks=vendor_option.estimated_implementation_weeks,
            capability_coverage=vendor_option.capability_match_percentage or 0.0,
            pros=pros,
            cons=cons,
            risk_factors=vendor_option.get_capability_gaps(),
            strategic_fit_score=vendor_option.strategic_fit_score or 0.0,
            recommendation_rank=vendor_option.ranking or 0,
        )

    def _create_vendor_option_from_product(self, product, capability) -> SolutionOption:
        """Create a SolutionOption from a VendorProduct."""
        return SolutionOption(
            id=f"product_{product.id}",
            name=product.name if hasattr(product, "name") else "Product",
            vendor_name=product.organization.name
            if hasattr(product, "organization") and product.organization
            else None,
            vendor_id=product.id,
            option_type="vendor",
            description=product.description if hasattr(product, "description") else None,
            total_score=0.0,
            capability_coverage=75.0,  # Default estimate
            pros=["Established vendor", "Standard implementation"],
            cons=["Requires customization", "Vendor lock-in potential"],
        )

    def _generate_build_option(self, capability, requirements, constraints) -> SolutionOption:
        """Generate a build/custom development option."""
        return SolutionOption(
            id="build_option",
            name="Custom Development",
            option_type="build",
            description=f"Build custom solution for {capability.name}",
            total_score=0.0,
            cost_estimate=150000.0,  # Default estimate
            implementation_weeks=24,
            capability_coverage=95.0,
            pros=[
                "Full customization to requirements",
                "No vendor lock-in",
                "Complete IP ownership",
                "Exact fit for business needs",
            ],
            cons=[
                "Higher initial development cost",
                "Longer implementation timeline",
                "Requires internal technical expertise",
                "Ongoing maintenance burden",
            ],
            strategic_fit_score=70.0,
        )

    def _generate_hybrid_option(
        self, capability, options: List[SolutionOption], constraints
    ) -> SolutionOption:
        """Generate a hybrid option combining vendor and custom elements."""
        return SolutionOption(
            id="hybrid_option",
            name="Hybrid Solution",
            option_type="hybrid",
            description=f"Combination of vendor product with custom extensions for {capability.name}",
            total_score=0.0,
            cost_estimate=100000.0,
            implementation_weeks=16,
            capability_coverage=90.0,
            pros=[
                "Balanced cost and customization",
                "Faster time to market than full build",
                "Leverages vendor best practices",
                "Allows targeted customization",
            ],
            cons=[
                "More complex architecture",
                "Dual maintenance streams",
                "Integration complexity",
                "Partial vendor dependency",
            ],
            strategic_fit_score=75.0,
        )

    # =========================================================================
    # Helper Methods - Scoring and Ranking
    # =========================================================================

    def _score_vendor_options(
        self, options: List[SolutionOption], capability, constraints: Optional[Dict] = None
    ) -> List[SolutionOption]:
        """Score vendor options based on criteria."""
        for option in options:
            option.total_score = self._calculate_option_score(option, self.DEFAULT_SCORING_WEIGHTS)

        # Sort by score
        options.sort(key=lambda x: x.total_score, reverse=True)

        # Assign ranks
        for idx, option in enumerate(options, 1):
            option.recommendation_rank = idx

        return options


    # How much retrieved governance context may move an option's score, as a
    # fraction of the 0-100 scale the other components use. Deliberately modest:
    # alignment with a principle is a tie-breaker between comparable options, not
    # a reason to promote an otherwise poor one above a strong one.
    GOVERNANCE_WEIGHT = 15.0

    # Words too generic to constitute evidence of alignment.
    _GOVERNANCE_STOPWORDS = frozenset("""
        the a an and or of to for in on with by is are be as at from that this it
        must should will shall we our all any use used using can may not no non
        system systems solution solutions service services data application
        applications architecture architectures technology technologies business
        platform platforms new existing standard standards approach approaches
    """.split())

    @classmethod
    def _significant_terms(cls, text: str) -> set:
        """Lowercased words of 4+ characters that are not stopwords."""
        import re as _re

        return {
            w for w in _re.findall(r"[a-z][a-z0-9\-]{3,}", (text or "").lower())
            if w not in cls._GOVERNANCE_STOPWORDS
        }

    def _score_governance_alignment(self, option: "SolutionOption", rag_context: Optional[Dict]):
        """Return (0.0-1.0 alignment, [human-readable reasons]) for *option*.

        Deliberately term-overlap rather than embeddings. get_context_for_solution
        returns plain rows from four SQL queries, there is no vector index behind
        it, and an opaque similarity number would be worse than useless here: an
        architect has to be able to see WHICH principle or prior decision moved an
        option up the list, or the ranking cannot be defended in an ARB.
        """
        if not rag_context:
            return 0.0, []

        option_terms = self._significant_terms(
            " ".join(filter(None, [
                option.name, option.description, option.vendor_name,
                " ".join(option.pros or []), " ".join(option.cons or []),
            ]))
        )
        if not option_terms:
            return 0.0, []

        sources = [
            ("principle", rag_context.get("principles") or []),
            ("prior ARB decision", rag_context.get("prior_decisions") or []),
            ("reference architecture", rag_context.get("reference_architectures") or []),
            ("established pattern", rag_context.get("existing_patterns") or []),
        ]

        matches, notes = 0, []
        for label, rows in sources:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = row.get("name") or row.get("title") or ""
                body = row.get("description") or row.get("summary") or ""
                shared = option_terms & self._significant_terms(f"{name} {body}")
                # Two shared significant terms: one is coincidence often enough
                # that a single overlap would make every option look aligned.
                if len(shared) >= 2:
                    matches += 1
                    notes.append(
                        f"aligns with {label} '{name or 'unnamed'}' "
                        f"({', '.join(sorted(shared)[:3])})"
                    )

        if not matches:
            return 0.0, []
        # Saturating: three corroborating sources is full alignment. Without a cap
        # an option matching many near-duplicate principles would dominate.
        return min(1.0, matches / 3.0), notes[:5]

    def _calculate_option_score(self, option: SolutionOption, weights: Dict[str, float]) -> float:
        """Calculate weighted score for an option."""
        score = 0.0

        # Cost score (inverse - lower is better)
        cost = option.cost_estimate or 100000
        cost_score = max(0, 100 - (cost / 5000))  # Simple normalization
        score += cost_score * weights.get("cost", 0.25)

        # Capability coverage
        coverage_score = option.capability_coverage or 0
        score += coverage_score * weights.get("capability_coverage", 0.25)

        # Risk score (based on number of cons)
        risk_score = max(0, 100 - (len(option.cons) * 15))
        score += risk_score * weights.get("risk", 0.20)

        # Strategic fit
        strategic_score = option.strategic_fit_score or 50
        score += strategic_score * weights.get("strategic_fit", 0.15)

        # Implementation score (inverse of weeks)
        weeks = option.implementation_weeks or 20
        impl_score = max(0, 100 - (weeks * 3))
        score += impl_score * weights.get("implementation", 0.15)

        return round(score, 2)

    def _rank_options(
        self,
        options: List[SolutionOption],
        constraints: Optional[Dict] = None,
        rag_context: Optional[Dict] = None,
    ) -> List[SolutionOption]:
        """Rank options based on scores, constraints and governance alignment.

        With no rag_context this is byte-for-byte the previous behaviour: the
        alignment term is zero and nothing is added. That matters because the
        endpoint retrieves context best-effort and swallows failures, so ranking
        must not depend on retrieval having succeeded.
        """
        # Score all options
        for option in options:
            if option.total_score == 0:
                option.total_score = self._calculate_option_score(
                    option, self.DEFAULT_SCORING_WEIGHTS
                )
            alignment, notes = self._score_governance_alignment(option, rag_context)
            if alignment:
                option.governance_alignment = round(alignment, 3)
                option.governance_notes = notes
                option.total_score = round(
                    option.total_score + alignment * self.GOVERNANCE_WEIGHT, 2
                )

        # Sort by score
        options.sort(key=lambda x: x.total_score, reverse=True)

        # Assign ranks
        for idx, option in enumerate(options, 1):
            option.recommendation_rank = idx

        return options

    # =========================================================================
    # Helper Methods - ARB Content Generation
    # =========================================================================

    def _generate_arb_title(self, capability, option: Optional[Dict] = None) -> str:
        """Generate ARB submission title."""
        if option:
            return (
                f"Solution Implementation: {option.get('name', 'Solution')} for {capability.name}"
            )
        return f"Capability Enhancement: {capability.name}"

    def _generate_arb_description(self, capability, option: Optional[Dict] = None) -> str:
        """Generate ARB submission description."""
        desc = f"This submission proposes a solution to address the {capability.name} capability."
        if option:
            desc += f" The recommended approach is to implement {option.get('name', 'the selected solution')}."
        return desc

    def _generate_business_justification(
        self, capability, option: Optional[Dict], context: Optional[Dict]
    ) -> str:
        """Generate business justification section."""
        scope_problem = (context or {}).get('scope', {}).get('problem', '')
        driver_section = f'## Business Driver\n{scope_problem}\n\n' if scope_problem else ''
        justification = driver_section + f"""
## Business Justification

### Strategic Alignment
The {capability.name} capability is classified as {capability.strategic_importance or 'important'} to the organization's strategic objectives. This initiative directly supports our enterprise architecture roadmap and capability development goals.

### Business Need
Current capability coverage is below target levels, creating operational inefficiencies and limiting business agility. Investment in this area will enable:
- Improved operational efficiency
- Enhanced business agility
- Reduced technical debt
- Better alignment with industry standards

### Expected Benefits
- Increased capability coverage
- Streamlined business processes
- Reduced manual workarounds
- Improved data quality and consistency
"""
        if option:
            justification += f"""
### Recommended Solution
The recommended solution is {option.get('name', 'the proposed implementation')}, which provides:
- Capability coverage of approximately {option.get('capability_coverage', 'significant')}%
- Implementation timeline of {option.get('implementation_weeks', 'reasonable')} weeks
- Estimated investment of ${option.get('cost_estimate', 'TBD'):,.0f}
"""
        return justification.strip()

    def _generate_technical_assessment(
        self, capability, option: Optional[Dict], alternatives: Optional[List[Dict]]
    ) -> str:
        """Generate technical assessment section."""
        assessment = f"""
## Technical Assessment

### Current State
The {capability.name} capability is currently supported by existing applications with varying levels of coverage and maturity.

### Proposed Architecture
"""
        if option:
            assessment += f"""
The recommended solution ({option.get('name', 'Selected Solution')}) will integrate with existing enterprise architecture through:
- Standard API interfaces
- Event-driven integration patterns
- Compliance with enterprise security standards

#### Technical Strengths
"""
            for pro in option.get("pros", ["Standard implementation", "Vendor support"]):
                assessment += f"- {pro}\n"

            assessment += "\n#### Technical Considerations\n"
            for con in option.get("cons", ["Requires integration effort"]):
                assessment += f"- {con}\n"

        if alternatives:
            assessment += "\n### Alternative Options Considered\n"
            for alt in alternatives[:2]:
                assessment += (
                    f"- **{alt.get('name', 'Alternative')}**: {alt.get('description', 'N/A')}\n"
                )

        return assessment.strip()

    def _generate_risk_analysis(
        self, option: Optional[Dict], alternatives: Optional[List[Dict]]
    ) -> str:
        """Generate risk analysis section."""
        analysis = """
## Risk Analysis

### Key Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Integration complexity | Medium | High | Phased integration approach with thorough testing |
| Resource availability | Medium | Medium | Early resource planning and contingency allocation |
| Scope creep | Low | High | Strict change control process |
| Vendor dependency | Medium | Medium | Contract terms and exit strategy planning |
"""

        if option and option.get("cons"):
            analysis += "\n### Solution-Specific Risks\n"
            for con in option.get("cons", []):
                analysis += f"- {con}\n"

        return analysis.strip()

    def _generate_implementation_approach(self, capability, option: Optional[Dict]) -> str:
        """Generate implementation approach section."""
        approach = """
## Implementation Approach

### Phased Implementation
The implementation will follow a phased approach to minimize risk and ensure successful adoption:

#### Phase 1: Foundation (Weeks 1 - 4)
- Environment setup and configuration
- Core integration development
- Initial testing and validation

#### Phase 2: Core Implementation (Weeks 5 - 12)
- Primary functionality deployment
- User acceptance testing
- Training and documentation

#### Phase 3: Optimization (Weeks 13 - 16)
- Performance tuning
- Additional feature deployment
- Go-live support

### Success Criteria
- Capability coverage improvement to target level
- Successful integration with existing systems
- User adoption metrics met
- Performance benchmarks achieved
"""
        return approach.strip()

    def _generate_cost_estimates(
        self, option: Optional[Dict], alternatives: Optional[List[Dict]]
    ) -> Dict[str, Any]:
        """Generate cost estimates section."""
        estimates = {
            "summary": {
                "total_estimated": option.get("cost_estimate", 100000) if option else 100000,
                "currency": "USD",
                "confidence": "medium",
            },
            "breakdown": {
                "software_licensing": 40000,
                "implementation_services": 30000,
                "internal_resources": 20000,
                "training": 5000,
                "contingency": 5000,
            },
            "recurring_annual": {"maintenance": 10000, "support": 5000, "hosting": 3000},
        }

        if option:
            estimates["summary"]["total_estimated"] = option.get("cost_estimate", 100000)

        return estimates

    def _generate_capability_impacts(self, capability) -> List[Dict[str, Any]]:
        """Generate capability impact analysis."""
        return [
            {
                "capability_id": capability.id,
                "capability_name": capability.name,
                "impact_type": "enhances",
                "impact_level": "high",
                "impact_description": f"Directly enhances {capability.name} coverage and maturity",
            }
        ]

    # =========================================================================
    # Helper Methods - Comparison and Insights
    # =========================================================================

    def _generate_comparison_matrix(self, options: List[SolutionOption]) -> Dict[str, Any]:
        """Generate a comparison matrix for options."""
        criteria = ["cost_estimate", "capability_coverage", "implementation_weeks", "total_score"]

        matrix = {"criteria": criteria, "options": []}

        for option in options:
            option_data = {
                "name": option.name,
                "values": {
                    "cost_estimate": option.cost_estimate,
                    "capability_coverage": option.capability_coverage,
                    "implementation_weeks": option.implementation_weeks,
                    "total_score": option.total_score,
                },
            }
            matrix["options"].append(option_data)

        return matrix

    def _generate_comparison_insights(self, options: List[SolutionOption]) -> List[Dict[str, Any]]:
        """Generate insights from option comparison."""
        insights = []

        if not options:
            return insights

        # Winner insight
        winner = options[0]
        insights.append(
            {
                "type": "recommendation",
                "title": "Recommended Option",
                "message": f"{winner.name} scores highest overall with a score of {winner.total_score}",
            }
        )

        # Cost comparison
        costs = [o.cost_estimate for o in options if o.cost_estimate]
        if costs:
            min_cost = min(costs)
            max_cost = max(costs)
            if max_cost > min_cost * 1.5:
                insights.append(
                    {
                        "type": "cost",
                        "title": "Significant Cost Variation",
                        "message": f"Options range from ${min_cost:,.0f} to ${max_cost:,.0f}",
                    }
                )

        # Coverage comparison
        coverages = [o.capability_coverage for o in options if o.capability_coverage]
        if coverages:
            max_coverage = max(coverages)
            if max_coverage < 80:
                insights.append(
                    {
                        "type": "coverage",
                        "title": "Coverage Gap",
                        "message": f"Maximum coverage is {max_coverage}%. Consider additional solutions.",
                    }
                )

        return insights

    def _generate_recommendation(self, capability, options: List[SolutionOption]) -> Dict[str, Any]:
        """Generate final recommendation."""
        if not options:
            return {"message": "No options available for recommendation", "confidence": 0}

        winner = options[0]

        return {
            "recommended_option": winner.name,
            "recommendation_rank": 1,
            "total_score": winner.total_score,
            "confidence": 0.85 if winner.total_score > 60 else 0.65,
            "rationale": f"Based on comprehensive analysis, {winner.name} provides the best balance of cost, capability coverage, and strategic fit for {capability.name}.",
            "key_factors": [
                f"Capability coverage: {winner.capability_coverage}%",
                f"Estimated cost: ${winner.cost_estimate:,.0f}"
                if winner.cost_estimate
                else "Cost: TBD",
                f"Implementation timeline: {winner.implementation_weeks} weeks"
                if winner.implementation_weeks
                else "Timeline: TBD",
            ],
        }

    # =========================================================================
    # Helper Methods - Gap Analysis
    # =========================================================================

    def _calculate_gap_severity(
        self, gap: float, strategic_importance: str, criticality: str
    ) -> str:
        """Calculate gap severity based on coverage and importance."""
        if gap <= 0:
            return "none"

        if criticality == "mission_critical" or strategic_importance == "critical":
            if gap > 50:
                return "critical"
            elif gap > 25:
                return "high"
            return "medium"

        if gap > 75:
            return "high"
        elif gap > 50:
            return "medium"
        return "low"

    def _generate_gap_description(self, capability, current: float, target: float) -> str:
        """Generate a description of the capability gap."""
        gap = target - current

        if gap <= 0:
            return f"{capability.name} meets or exceeds target coverage."

        return (
            f"{capability.name} has a coverage gap of {gap:.1f}%. "
            f"Current coverage is {current:.1f}% against a target of {target:.1f}%. "
            f"Investment in solutions is recommended to address this gap."
        )

    def _estimate_gap_investment(self, gap: float, capability) -> float:
        """Estimate investment needed to close a capability gap."""
        # Simple estimation model — baseline figure for gap investment calculation
        base_cost = 50000  # fabricated-ok: baseline parameter of the documented gap-investment estimation model, not a measured figure

        # Adjust based on gap size
        if gap > 75:
            multiplier = 3.0
        elif gap > 50:
            multiplier = 2.0
        elif gap > 25:
            multiplier = 1.5
        else:
            multiplier = 1.0

        # Adjust for strategic importance
        if capability.strategic_importance == "critical":
            multiplier *= 1.5
        elif capability.strategic_importance == "high":
            multiplier *= 1.2

        return round(base_cost * multiplier, -3)  # Round to nearest 1000

    def apply_option(
        self,
        option_id: str,
        canvas_id: int,
        user_id: int,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Apply a solution option to a canvas.

        Args:
            option_id: ID of the option to apply
            canvas_id: ID of the canvas to apply to
            user_id: ID of the user applying the option
            request_id: Optional request ID for tracking

        Returns:
            Dict with canvas_id, validation_result, applied_changes, audit_id
        """
        try:
            # Idempotency: return existing audit if this option has already been applied for the same request or user/canvas
            existing = None
            if request_id:
                existing = (
                    AIRecommendation.query.filter_by(option_id=option_id, request_id=request_id)
                    .order_by(AIRecommendation.created_at.desc())
                    .first()
                )

            if not existing:
                existing = (
                    AIRecommendation.query.filter_by(
                        option_id=option_id, canvas_id=canvas_id, user_id=user_id
                    )
                    .order_by(AIRecommendation.created_at.desc())
                    .first()
                )

            if existing:
                applied_changes = existing.applied_changes
                validation_result = existing.validation_result
                # Normalize stored JSON (handle older stringified values)
                try:
                    if isinstance(applied_changes, str):
                        applied_changes = json.loads(applied_changes)
                except Exception as e:
                    logger.error("Failed to parse stored applied_changes JSON for option %s: %s", option_id, e)
                try:
                    if isinstance(validation_result, str):
                        validation_result = json.loads(validation_result)
                except Exception as e:
                    logger.error("Failed to parse stored validation_result JSON for option %s: %s", option_id, e)

                audit_id = f"audit_{existing.id}"
                logger.info(f"apply_option idempotent hit for {option_id}, audit_id: {audit_id}")
                return {
                    "canvas_id": canvas_id,
                    "validation_result": validation_result,
                    "applied_changes": applied_changes,
                    "audit_id": audit_id,
                    "message": "already_applied",
                }

            # Retrieve option details from previous AI recommendation
            prior = (
                AIRecommendation.query.filter_by(option_id=option_id)
                .order_by(AIRecommendation.created_at.desc())
                .first()
            )
            if prior and prior.recommendation_data:
                option_data = prior.recommendation_data
            else:
                error_msg = f"Option not found: no recommendation data for option_id={option_id}"
                logger.error(error_msg)
                return {"error": error_msg}

            # Server-side validation: verify referenced application entities exist
            for ent in option_data.get("entities", []):
                etype = ent.get("entity_type")
                eid = ent.get("entity_id")
                if etype == "application":
                    found = False
                    # Try a few common application model modules
                    for mod_name, cls_name in [
                        ("app.models.application_component", "ApplicationComponent"),
                        ("app.models.application_component_fast", "ApplicationComponent"),
                        ("app.models.application_portfolio", "ApplicationComponent"),
                    ]:
                        try:
                            mod = __import__(mod_name, fromlist=[cls_name])
                            Model = getattr(mod, cls_name, None)
                            if Model:
                                rec = db.session.get(Model, eid)
                                if rec:
                                    found = True
                                    break
                        except Exception:
                            continue

                    if not found:
                        error_msg = f"Referenced application entity not found: {eid}"
                        logger.error(error_msg)
                        return {"error": error_msg}

            applied_changes = []

            # Apply actions to canvas via solution composer API (mocked here) and record what would be applied
            for action in option_data.get("actions", []):
                if action.get("type") == "add_node":
                    node_id = f"node_{len(applied_changes) + 1}"
                    applied_changes.append(
                        {"action": "add_node", "node_id": node_id, "payload": action.get("payload")}
                    )
                elif action.get("type") == "add_connection":
                    connection_id = f"conn_{len(applied_changes) + 1}"
                    applied_changes.append(
                        {
                            "action": "add_connection",
                            "connection_id": connection_id,
                            "payload": action.get("payload"),
                        }
                    )

            # Basic entity existence validation (not full solution-composer validation)
            validation_issues = []
            for change in applied_changes:
                payload = change.get("payload", {})
                entity_id = payload.get("entity_id")
                if entity_id and change.get("action") == "add_node":
                    try:
                        from app.models import ApplicationComponent
                        if not db.session.get(ApplicationComponent, entity_id):
                            validation_issues.append(f"Entity {entity_id} not found in database")
                    except Exception as val_err:
                        logger.debug(f"Validation lookup failed for entity {entity_id}: {val_err}")
            validation_result = {
                "valid": len(validation_issues) == 0,
                "issue_count": len(validation_issues),
                "issues": validation_issues,
                "validation_method": "basic_entity_check",
            }

            # Persist structured provenance in database
            prompt_hash = hashlib.sha256(
                f"{option_id}:{canvas_id}:{user_id}:{request_id or ''}".encode("utf - 8")
            ).hexdigest()

            ai_recommendation = AIRecommendation(
                option_id=option_id,
                canvas_id=canvas_id,
                user_id=user_id,
                request_id=request_id,
                recommendation_type="apply_option",
                recommendation_data=option_data,
                applied_changes=applied_changes,
                validation_result=validation_result,
                model_version=self.app.config.get("LLM_MODEL_VERSION", "1.0.0")
                if self.app
                else "1.0.0",
                prompt_template_id="apply_option",
                prompt_hash=prompt_hash,
                evidence_links=[
                    {
                        "entity_id": e.get("entity_id"),
                        "db_url": f"/{e.get('entity_type')}s/{e.get('entity_id')}",
                    }
                    for e in option_data.get("entities", [])
                ],
                llm_response_short=None,
                success=True,
                applied_at=datetime.utcnow(),
            )

            db.session.add(ai_recommendation)
            db.session.commit()

            audit_id = f"audit_{ai_recommendation.id}"

            logger.info(f"Applied option {option_id} to canvas {canvas_id}, audit_id: {audit_id}")

            return {
                "canvas_id": canvas_id,
                "validation_result": validation_result,
                "applied_changes": applied_changes,
                "audit_id": audit_id,
            }

        except Exception as e:
            logger.error(f"Error applying option {option_id}: {e}")
            raise
