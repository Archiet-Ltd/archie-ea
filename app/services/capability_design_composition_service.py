"""
Capability Design Composition Service

Composes existing capability services into a single analysis result
for solution design. Aggregates coverage, gaps, vendor recommendations,
domain health, and input quality into one coherent response.

Used by: ARC-F03 (Capability-Driven Design composition layer)
"""

import json
import logging
from typing import Any, Dict, List

from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.solution_models import Solution, SolutionCapabilityMapping
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

logger = logging.getLogger(__name__)


UNKNOWN_FACTORS = [
    {"factor": "Vendor pricing", "description": "No pricing or license cost data is tracked for vendor products"},
    {"factor": "License entitlements", "description": "Existing license agreements and unused entitlements are not modeled"},
    {"factor": "Team capacity", "description": "Development and operations team availability is not tracked"},
    {"factor": "Procurement timeline", "description": "RFI/RFP/contract negotiation timelines are not estimated"},
    {"factor": "Political constraints", "description": "Organizational politics and stakeholder preferences are not captured"},
    {"factor": "Vendor negotiation leverage", "description": "Existing vendor relationships and negotiation history not modeled"},
    {"factor": "Regulatory approval", "description": "Regulatory review and approval timelines not estimated"},
    {"factor": "Change management effort", "description": "User training, communication, and adoption effort not quantified"},
]


class CapabilityDesignCompositionService:
    """Composes capability analysis from multiple services for solution design."""

    def analyze(self, solution_id: int) -> Dict[str, Any]:
        """
        Produce a composite capability analysis for a solution.

        Args:
            solution_id: ID of the solution to analyze.

        Returns:
            Dict with 7 keys: selected_capabilities, coverage_matrix,
            tech_gaps, vendor_recommendations, domain_health,
            input_quality_report, unknown_factors.
        """
        solution = db.session.get(Solution, solution_id)
        if not solution:
            logger.warning("Solution %s not found for capability analysis", solution_id)
            return {
                "selected_capabilities": [],
                "coverage_matrix": [],
                "tech_gaps": {},
                "vendor_recommendations": [],
                "domain_health": [],
                "input_quality_report": [],
                "unknown_factors": UNKNOWN_FACTORS,
            }

        # Capabilities linked to this solution. The former problem-overlap branch
        # joined solution_problem_definitions by solution_id, which does not exist
        # (that table links via session_id), so it is dropped — the direct
        # solution_id mapping is the correct, working source.
        mappings = SolutionCapabilityMapping.query.filter_by(  # tenant-filtered: scoped via solution_id FK
            solution_id=solution_id
        ).all()

        selected_capabilities = self._build_selected_capabilities(mappings)
        capability_ids = [m.capability_id for m in mappings]

        coverage_matrix = self._build_coverage_matrix(capability_ids)
        tech_gaps = self._build_tech_gaps(capability_ids)
        vendor_recommendations = self._build_vendor_recommendations(capability_ids, coverage_matrix)
        domain_health = self._build_domain_health()
        input_quality_report = self._build_input_quality_report(coverage_matrix)

        return {
            "selected_capabilities": selected_capabilities,
            "coverage_matrix": coverage_matrix,
            "tech_gaps": tech_gaps,
            "vendor_recommendations": vendor_recommendations,
            "domain_health": domain_health,
            "input_quality_report": input_quality_report,
            "unknown_factors": UNKNOWN_FACTORS,
        }

    def discover_capabilities_from_problem(self, problem_text: str) -> List[Dict]:
        """
        Discover relevant capabilities from a natural-language problem statement.

        Uses LLM to map business problem text to existing capabilities in the
        platform. Returns top 10 matches with confidence >= 0.5.
        ARC-I01: Problem-first capability discovery via AI.
        """
        from app.models.unified_capability import UnifiedCapability
        from app.services.llm_service import LLMService

        # Load L1-L2 capabilities for breadth (prioritise higher-level)
        capabilities = (
            UnifiedCapability.active()
            .filter(UnifiedCapability.status == "defined")
            .order_by(UnifiedCapability.level.asc().nullslast())
            .limit(50)
            .all()
        )
        if not capabilities:
            logger.warning("No active capabilities found for problem discovery")
            return []

        capability_context = "\n".join(
            f"- ID {cap.id}: {cap.name} | Domain: {cap.domain.code if cap.domain else 'N/A'} | Desc: {cap.description[:100] if cap.description else 'No description'}..."
            for cap in capabilities
        )

        prompt = f"""You are an enterprise architecture expert specialising in capability-based planning.

BUSINESS PROBLEM:
{problem_text}

AVAILABLE BUSINESS CAPABILITIES:
{capability_context}

TASK:
Identify which business capabilities are most affected by or relevant to solving this problem.
For each, explain WHY this capability is relevant to the problem.
Return the top 10 most relevant, ranked by confidence.

RESPOND WITH VALID JSON ONLY (no markdown fences):
{{
    "suggestions": [
        {{
            "capability_id": 123,
            "capability_name": "Example Capability",
            "confidence": 0.9,
            "reasoning": "This capability is directly impacted because..."
        }}
    ]
}}"""

        try:
            provider, model = LLMService._get_configured_provider()
            response, _interaction = LLMService._call_llm_with_failover(
                prompt=prompt,
                model=model,
                provider=provider,
            )

            # Strip markdown fences if present
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            result = json.loads(text)
            suggestions = result.get("suggestions", [])

            # Validate capability IDs exist in the DB set
            valid_ids = {cap.id for cap in capabilities}
            validated = []
            for s in suggestions:
                cid = s.get("capability_id")
                conf = s.get("confidence", 0)
                if cid in valid_ids and conf >= 0.5:
                    validated.append({
                        "capability_id": cid,
                        "capability_name": s.get("capability_name", ""),
                        "confidence": round(conf, 2),
                        "reasoning": s.get("reasoning", ""),
                    })

            validated.sort(key=lambda x: x["confidence"], reverse=True)
            logger.info(
                f"Problem discovery returned {len(validated)} capabilities "
                f"(from {len(suggestions)} raw suggestions)"
            )
            return validated[:10]

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response for problem discovery: {e}")
            return []
        except Exception as e:
            logger.error(f"Error in problem-first capability discovery: {e}")
            return []

    # ------------------------------------------------------------------
    # Private builders — each wrapped in try/except for partial results
    # ------------------------------------------------------------------

    def _build_selected_capabilities(
        self, mappings: List[SolutionCapabilityMapping]
    ) -> List[Dict[str, Any]]:
        """Return structured list of capabilities linked to the solution."""
        results = []
        for mapping in mappings:
            cap = db.session.get(BusinessCapability, mapping.capability_id)
            if not cap:
                continue
            results.append({
                "mapping_id": mapping.id,
                "capability_id": cap.id,
                "capability_name": cap.name,
                "business_domain": getattr(cap, "business_domain", None),  # model-safety-ok
                "level": getattr(cap, "level", None),  # model-safety-ok
                "support_level": mapping.support_level,
                "priority": mapping.priority,
                "coverage_percentage": mapping.coverage_percentage,
                "maturity_current": mapping.maturity_current,
                "maturity_target": mapping.maturity_target,
            })
        return results

    def _build_coverage_matrix(self, capability_ids: List[int]) -> List[Dict[str, Any]]:
        """
        For each selected capability, find which applications support it
        via UnifiedApplicationCapabilityMapping. Returns coverage %, support
        level, and maturity per app-capability pair.
        """
        try:
            if not capability_ids:
                return []

            # UnifiedApplicationCapabilityMapping uses unified_capability_id.
            # We need to find unified capabilities that correspond to the
            # business capabilities selected for the solution.
            from app.models.unified_capability import UnifiedCapability

            # Map business capability names to unified capabilities
            bus_caps = BusinessCapability.query.filter(
                BusinessCapability.id.in_(capability_ids)
            ).all()
            bus_cap_names = {cap.id: cap.name for cap in bus_caps}

            # Find unified capabilities by matching name
            unified_caps = UnifiedCapability.active().filter(
                UnifiedCapability.name.in_(list(bus_cap_names.values()))
            ).all()
            unified_cap_map = {uc.name: uc for uc in unified_caps}

            results = []
            for bus_cap_id, bus_cap_name in bus_cap_names.items():
                uc = unified_cap_map.get(bus_cap_name)
                if not uc:
                    results.append({
                        "capability_id": bus_cap_id,
                        "capability_name": bus_cap_name,
                        "applications": [],
                        "total_apps": 0,
                    })
                    continue

                app_mappings = UnifiedApplicationCapabilityMapping.query.filter_by(  # model-safety-ok
                    unified_capability_id=uc.id
                ).all()

                from app.models.application_portfolio import ApplicationComponent

                apps = []
                for am in app_mappings:
                    app = db.session.get(ApplicationComponent, am.application_component_id)
                    if not app:
                        continue
                    apps.append({
                        "application_id": app.id,
                        "application_name": app.name,
                        "support_level": am.support_level,
                        "coverage_percentage": am.coverage_percentage,
                        "maturity_level": am.maturity_level,
                        "is_strategic": am.is_strategic,
                    })

                results.append({
                    "capability_id": bus_cap_id,
                    "capability_name": bus_cap_name,
                    "applications": apps,
                    "total_apps": len(apps),
                })

            return results

        except Exception:
            logger.exception("Failed to build coverage matrix")
            return []

    def _build_tech_gaps(self, capability_ids: List[int]) -> Dict[str, Any]:
        """
        Retrieve ACM domain gaps filtered to relevant capabilities.
        Uses ACMTechnicalCapabilityService.analyze_capability_gaps().
        """
        try:
            from app.modules.capabilities.services.acm_technical_capability_service import (
                ACMTechnicalCapabilityService,
            )

            full_gaps = ACMTechnicalCapabilityService.analyze_capability_gaps()

            if not capability_ids:
                return full_gaps

            # Filter gap results to only capabilities relevant to the solution.
            # The full_gaps dict contains domain-keyed entries with capability lists.
            bus_caps = BusinessCapability.query.filter(
                BusinessCapability.id.in_(capability_ids)
            ).all()
            {cap.name.lower() for cap in bus_caps}
            relevant_domains = {
                getattr(cap, "business_domain", None)  # model-safety-ok
                for cap in bus_caps
            }
            relevant_domains.discard(None)

            filtered = {}
            domains = full_gaps.get("domains", [])
            filtered_domains = []
            for domain_entry in domains:
                domain_name = domain_entry.get("domain", "")
                if domain_name in relevant_domains:
                    filtered_domains.append(domain_entry)

            filtered["domains"] = filtered_domains
            filtered["total_gaps"] = sum(
                len(d.get("gaps", [])) for d in filtered_domains
            )
            filtered["unfiltered_total"] = full_gaps.get("total_gaps", 0)

            return filtered

        except Exception:
            logger.exception("Failed to build tech gaps analysis")
            return {}

    def _build_vendor_recommendations(
        self,
        capability_ids: List[int],
        coverage_matrix: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        For capabilities with coverage gaps, recommend vendors via
        CapabilityBasedVendorSelector.find_vendors_for_capability().
        """
        try:
            from app.modules.vendors.services.capability_based_vendor_selector import (
                CapabilityBasedVendorSelector,
            )

            # Identify capabilities with low or no coverage
            gap_cap_ids = []
            for entry in coverage_matrix:
                total_apps = entry.get("total_apps", 0)
                if total_apps == 0:
                    gap_cap_ids.append(entry["capability_id"])
                    continue
                # Check if average coverage is below 50%
                apps = entry.get("applications", [])
                if apps:
                    avg_cov = sum(
                        (a.get("coverage_percentage") or 0) for a in apps
                    ) / len(apps)
                    if avg_cov < 50:
                        gap_cap_ids.append(entry["capability_id"])

            if not gap_cap_ids:
                return []

            selector = CapabilityBasedVendorSelector()
            recommendations = []
            for cap_id in gap_cap_ids:
                try:
                    vendors = selector.find_vendors_for_capability(
                        capability_id=cap_id, min_coverage=30
                    )
                    if vendors:
                        cap = db.session.get(BusinessCapability, cap_id)
                        recommendations.append({
                            "capability_id": cap_id,
                            "capability_name": cap.name if cap else f"Capability {cap_id}",
                            "vendor_options": vendors,
                        })
                except Exception:
                    logger.exception(
                        "Vendor lookup failed for capability %s", cap_id
                    )

            return recommendations

        except Exception:
            logger.exception("Failed to build vendor recommendations")
            return []

    def _build_domain_health(self) -> List[Dict[str, Any]]:
        """
        Retrieve domain health scores from CapabilityHeatmapService.
        """
        try:
            from app.modules.capabilities.services.capability_heatmap_service import (
                CapabilityHeatmapService,
            )

            service = CapabilityHeatmapService()
            return service.get_domain_health()

        except Exception:
            logger.exception("Failed to build domain health")
            return []

    def _build_input_quality_report(
        self, coverage_matrix: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        For each application appearing in the coverage matrix, report
        data_freshness_tier and completeness_score from ApplicationComponent.
        """
        try:
            from app.models.application_portfolio import ApplicationComponent

            # Collect unique application IDs from coverage matrix
            seen_app_ids = set()
            for entry in coverage_matrix:
                for app_info in entry.get("applications", []):
                    seen_app_ids.add(app_info["application_id"])

            if not seen_app_ids:
                return []

            apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(list(seen_app_ids))
            ).all()

            report = []
            for app in apps:
                report.append({
                    "application_id": app.id,
                    "application_name": app.name,
                    "data_freshness_tier": app.data_freshness_tier,  # model-safety-ok
                    "completeness_score": app.completeness_score,  # model-safety-ok
                })

            return report

        except Exception:
            logger.exception("Failed to build input quality report")
            return []
