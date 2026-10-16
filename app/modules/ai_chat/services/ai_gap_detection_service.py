"""
-> app.modules.ai_chat.services.ai_analysis_service

AI-Powered Gap Detection Query Service

Provides natural language query interface for gap analysis and proactive gap detection.
Designed for Enterprise Architects to quickly identify capability gaps, rationalization
opportunities, and architectural issues.

Features:
- Natural language query processing
- Pre-built architect queries (coverage, rationalization, lifecycle, vendor)
- Proactive gap alerts and recommendations
- Multi-dimensional gap scoring and prioritization
- Integration with existing CapabilityGapAnalysis models

Usage:
    service = AIGapDetectionService()

    # Natural language query
    results = service.query("Show capabilities with less than 50% coverage")

    # Pre-built queries
    low_coverage = service.find_low_coverage_capabilities(threshold=50)
    rationalization = service.find_rationalization_opportunities()
    legacy_only = service.find_capabilities_with_only_legacy_apps()
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app
from sqlalchemy import func, or_  # dead-code-ok

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.capability_gap_analysis import (  # dead-code-ok
    CapabilityGapDetail,
)
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability  # dead-code-ok

logger = logging.getLogger(__name__)

BASE_SAVINGS_PER_APP_USD = 50000  # fabricated-ok: declared estimation baseline constant, not a measured value


class AIGapDetectionService:
    """
    AI-powered gap detection service with natural language query support.

    Provides architects with intelligent queries to identify:
    - Capabilities with insufficient application coverage
    - Rationalization opportunities (multiple apps, same capability)
    - Capabilities supported only by legacy applications
    - Vendor products approaching end-of-life
    - Strategic gaps based on business criticality
    """

    # Query patterns for natural language processing
    QUERY_PATTERNS = {
        "low_coverage": [
            r"capabilities?\s+with\s+(?:less\s+than|under|below)\s+(\d+)%?\s+coverage",
            r"capabilities?\s+(?:less\s+than|under|below)\s+(\d+)%?\s+coverage",
            r"(?:less\s+than|under|below)\s+(\d+)%?\s+coverage",
            r"under-?covered\s+capabilities?",
            r"gaps?\s+in\s+coverage",
            r"capabilities?\s+(?:that\s+)?(?:have|has)\s+gaps?",
        ],
        "rationalization": [
            r"rationalization\s+opportunities?",
            r"multiple\s+app(?:lication)?s?\s+(?:for\s+)?same\s+capability",
            r"redundant\s+app(?:lication)?s?",
            r"consolidation\s+opportunities?",
            r"duplicate\s+app(?:lication)?s?\s+(?:serving|supporting)",
        ],
        "legacy_only": [
            r"capabilities?\s+(?:with\s+)?only\s+legacy\s+app(?:lication)?s?",
            r"legacy[\s-]only\s+capabilities?",
            r"capabilities?\s+(?:supported\s+)?by\s+legacy\s+(?:only|systems?)",
            r"modernization\s+candidates?",
        ],
        "critical_gaps": [
            r"critical\s+(?:capability\s+)?gaps?",
            r"mission[\s-]critical\s+gaps?",
            r"high[\s-]priority\s+gaps?",
            r"strategic\s+gaps?",
        ],
        "vendor_lifecycle": [
            r"vendor\s+(?:products?\s+)?(?:at\s+)?end[\s-]of[\s-]life",
            r"eol\s+(?:vendor\s+)?products?",
            r"sunset(?:ting)?\s+(?:vendor\s+)?products?",
            r"deprecated\s+(?:vendor\s+)?(?:products?|software)",
        ],
        "no_coverage": [
            r"capabilities?\s+with(?:out)?\s+no\s+(?:app(?:lication)?s?|coverage)",
            r"uncovered\s+capabilities?",
            r"capabilities?\s+(?:that\s+)?lack\s+app(?:lication)?s?",
            r"orphan(?:ed)?\s+capabilities?",
        ],
    }

    def __init__(self):
        """Initialize the AI Gap Detection Service."""
        self.app = current_app._get_current_object() if current_app else None

    def query(self, natural_language_query: str) -> Dict[str, Any]:
        """
        Process a natural language query and return relevant gap analysis results.

        Args:
            natural_language_query: Natural language question about gaps

        Returns:
            Dictionary containing query results, insights, and recommendations
        """
        logger.info(f"Processing natural language query: {natural_language_query}")

        query_lower = natural_language_query.lower().strip()
        query_type, params = self._classify_query(query_lower)

        results = {
            "query": natural_language_query,
            "query_type": query_type,
            "timestamp": datetime.utcnow().isoformat(),
            "results": [],
            "summary": {},
            "recommendations": [],
        }

        if query_type == "low_coverage":
            threshold = params.get("threshold", 50)
            results["results"] = self.find_low_coverage_capabilities(threshold)
            results["summary"] = {
                "type": "Coverage Analysis",
                "threshold": threshold,
                "count": len(results["results"]),
                "insight": f"Found {len(results['results'])} capabilities with coverage below {threshold}%",
            }

        elif query_type == "rationalization":
            results["results"] = self.find_rationalization_opportunities()
            results["summary"] = {
                "type": "Rationalization Analysis",
                "count": len(results["results"]),
                "insight": f"Found {len(results['results'])} rationalization opportunities",
            }

        elif query_type == "legacy_only":
            results["results"] = self.find_capabilities_with_only_legacy_apps()
            results["summary"] = {
                "type": "Legacy Analysis",
                "count": len(results["results"]),
                "insight": f"Found {len(results['results'])} capabilities supported only by legacy applications",
            }

        elif query_type == "critical_gaps":
            results["results"] = self.find_critical_gaps()
            results["summary"] = {
                "type": "Critical Gap Analysis",
                "count": len(results["results"]),
                "insight": f"Found {len(results['results'])} critical/high-priority gaps",
            }

        elif query_type == "vendor_lifecycle":
            results["results"] = self.find_vendor_lifecycle_risks()
            results["summary"] = {
                "type": "Vendor Lifecycle Analysis",
                "count": len(results["results"]),
                "insight": f"Found {len(results['results'])} vendor products with lifecycle concerns",
            }

        elif query_type == "no_coverage":
            results["results"] = self.find_uncovered_capabilities()
            results["summary"] = {
                "type": "Coverage Gap Analysis",
                "count": len(results["results"]),
                "insight": f"Found {len(results['results'])} capabilities with no application coverage",
            }

        else:
            # Default: comprehensive gap summary
            results["results"] = self.get_comprehensive_gap_summary()
            results["summary"] = {
                "type": "Comprehensive Analysis",
                "insight": "Comprehensive gap analysis across all dimensions",
            }

        # Generate AI-powered recommendations
        results["recommendations"] = self._generate_recommendations(query_type, results["results"])

        return results

    def _classify_query(self, query: str) -> Tuple[str, Dict[str, Any]]:
        """
        Classify the natural language query into a query type.

        Returns:
            Tuple of (query_type, parameters)
        """
        params = {}

        for query_type, patterns in self.QUERY_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, query, re.IGNORECASE)
                if match:
                    # Extract parameters if present
                    if match.groups():
                        try:
                            params["threshold"] = int(match.group(1))
                        except (IndexError, ValueError):
                            logger.exception("Failed to operation")
                            pass
                    return query_type, params

        return "comprehensive", params

    def find_low_coverage_capabilities(self, threshold: int = 50) -> List[Dict[str, Any]]:
        """
        Find capabilities with coverage percentage below the specified threshold.

        Args:
            threshold: Coverage percentage threshold (default 50%)

        Returns:
            List of capabilities with low coverage, including details
        """
        results = []

        try:
            # Pre-load all active mappings in a single query to avoid N+1
            all_mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                is_active=True
            ).all()
            from collections import defaultdict

            mappings_by_cap = defaultdict(list)
            for m in all_mappings:
                mappings_by_cap[m.unified_capability_id].append(m)

            capabilities = UnifiedCapability.query.limit(2000).all()

            for capability in capabilities:
                mappings = mappings_by_cap.get(capability.id, [])

                total_coverage = 0
                app_count = 0

                for mapping in mappings:
                    if mapping.coverage_percentage:
                        total_coverage += mapping.coverage_percentage
                        app_count += 1

                avg_coverage = total_coverage / app_count if app_count > 0 else 0

                if avg_coverage < threshold:
                    results.append(
                        {
                            "capability_id": capability.id,
                            "capability_name": capability.name,
                            "capability_code": capability.code,
                            "level": capability.level,
                            "domain_id": capability.domain_id,
                            "strategic_importance": capability.strategic_importance,
                            "business_criticality": capability.business_criticality,
                            "current_coverage": round(avg_coverage, 2),
                            "target_coverage": 100,
                            "coverage_gap": round(100 - avg_coverage, 2),
                            "supporting_app_count": app_count,
                            "gap_severity": self._calculate_gap_severity(
                                avg_coverage,
                                capability.strategic_importance,
                                capability.business_criticality,
                            ),
                            "recommendation": self._get_coverage_recommendation(
                                avg_coverage, capability
                            ),
                        }
                    )

            # Sort by gap severity and coverage gap
            results.sort(
                key=lambda x: (-self._severity_score(x["gap_severity"]), -x["coverage_gap"])
            )

        except Exception as e:
            logger.error(f"Error finding low coverage capabilities: {e}")

        return results

    def find_rationalization_opportunities(self) -> List[Dict[str, Any]]:
        """
        Find capabilities served by multiple applications (rationalization candidates).

        Returns:
            List of rationalization opportunities with affected applications
        """
        results = []

        try:
            # Group mappings by capability
            capability_apps = (
                db.session.query(
                    UnifiedApplicationCapabilityMapping.unified_capability_id,
                    func.count(UnifiedApplicationCapabilityMapping.application_component_id).label(
                        "app_count"
                    ),
                )
                .filter(UnifiedApplicationCapabilityMapping.is_active == True)
                .group_by(UnifiedApplicationCapabilityMapping.unified_capability_id)
                .having(
                    func.count(UnifiedApplicationCapabilityMapping.application_component_id) > 1
                )
                .all()
            )

            # Batch prefetch capabilities and mappings to avoid N+1
            cap_ids_list = [cap_id for cap_id, _ in capability_apps]
            all_capabilities = UnifiedCapability.query.filter(
                UnifiedCapability.id.in_(cap_ids_list)
            ).all() if cap_ids_list else []
            cap_lookup = {c.id: c for c in all_capabilities}

            # Batch prefetch all active mappings for these capabilities
            all_mappings = UnifiedApplicationCapabilityMapping.query.filter(
                UnifiedApplicationCapabilityMapping.unified_capability_id.in_(cap_ids_list),
                UnifiedApplicationCapabilityMapping.is_active == True
            ).all() if cap_ids_list else []
            mappings_by_cap = {}
            for m in all_mappings:
                mappings_by_cap.setdefault(m.unified_capability_id, []).append(m)

            # Batch prefetch all referenced applications
            all_app_ids = list({m.application_component_id for m in all_mappings})
            all_apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(all_app_ids)
            ).all() if all_app_ids else []
            app_lookup = {a.id: a for a in all_apps}

            for cap_id, app_count in capability_apps:
                capability = cap_lookup.get(cap_id)
                if not capability:
                    continue

                # Get all applications supporting this capability (from prefetched data)
                mappings = mappings_by_cap.get(cap_id, [])

                apps_info = []

                for mapping in mappings:
                    app = app_lookup.get(mapping.application_component_id)
                    if app:
                        app_info = {
                            "app_id": app.id,
                            "app_name": app.name,
                            "coverage_percentage": mapping.coverage_percentage or 0,
                            "support_level": mapping.support_level,
                            "lifecycle_status": app.lifecycle_status or "unknown",
                        }
                        apps_info.append(app_info)

                # Calculate potential savings (simplified)
                estimated_savings = self._estimate_rationalization_savings(apps_info)

                results.append(
                    {
                        "capability_id": capability.id,
                        "capability_name": capability.name,
                        "capability_code": capability.code,
                        "level": capability.level,
                        "strategic_importance": capability.strategic_importance,
                        "application_count": app_count,
                        "applications": apps_info,
                        "estimated_annual_savings": estimated_savings,
                        "rationalization_priority": self._calculate_rationalization_priority(
                            app_count, capability.strategic_importance
                        ),
                        "recommendation": f"Consider consolidating {app_count} applications into a single solution",
                    }
                )

            # Sort by priority and savings
            results.sort(
                key=lambda x: (
                    -self._priority_score(x["rationalization_priority"]),
                    -x["estimated_annual_savings"],
                )
            )

        except Exception as e:
            logger.error(f"Error finding rationalization opportunities: {e}")

        return results

    def find_capabilities_with_only_legacy_apps(self) -> List[Dict[str, Any]]:
        """
        Find capabilities that are only supported by legacy applications.

        Returns:
            List of capabilities with only legacy support (modernization candidates)
        """
        results = []

        try:
            # Pre-load all active mappings and group by capability to avoid N+1
            all_mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                is_active=True
            ).all()
            from collections import defaultdict

            mappings_by_cap = defaultdict(list)
            app_ids_needed = set()
            for m in all_mappings:
                mappings_by_cap[m.unified_capability_id].append(m)
                app_ids_needed.add(m.application_component_id)

            # Pre-load all referenced apps in a single query
            all_apps = ApplicationComponent.query.filter(
                ApplicationComponent.id.in_(app_ids_needed)
            ).all() if app_ids_needed else []
            app_lookup = {app.id: app for app in all_apps}

            capabilities = UnifiedCapability.query.limit(2000).all()

            for capability in capabilities:
                mappings = mappings_by_cap.get(capability.id, [])

                if not mappings:
                    continue

                legacy_apps = []
                modern_apps = []

                for mapping in mappings:
                    app = app_lookup.get(mapping.application_component_id)
                    if not app:
                        continue

                    # Determine if app is legacy based on various indicators
                    is_legacy = self._is_legacy_application(app)

                    app_info = {
                        "app_id": app.id,
                        "app_name": app.name,
                        "lifecycle_status": app.lifecycle_status or "unknown",
                        "technology_age": getattr(app, "technology_age_years", None),  # model-safety-ok: field is technology_age_years on ApplicationComponent
                        "coverage_percentage": mapping.coverage_percentage or 0,
                    }

                    if is_legacy:
                        legacy_apps.append(app_info)
                    else:
                        modern_apps.append(app_info)

                # Only include if ALL supporting apps are legacy
                if legacy_apps and not modern_apps:
                    results.append(
                        {
                            "capability_id": capability.id,
                            "capability_name": capability.name,
                            "capability_code": capability.code,
                            "level": capability.level,
                            "strategic_importance": capability.strategic_importance,
                            "business_criticality": capability.business_criticality,
                            "legacy_app_count": len(legacy_apps),
                            "legacy_applications": legacy_apps,
                            "modernization_urgency": self._calculate_modernization_urgency(
                                capability, legacy_apps
                            ),
                            "recommendation": f"Modernization required: {len(legacy_apps)} legacy apps supporting critical capability",
                        }
                    )

            # Sort by urgency
            results.sort(key=lambda x: -self._urgency_score(x["modernization_urgency"]))

        except Exception as e:
            logger.error(f"Error finding legacy-only capabilities: {e}")

        return results

    def find_critical_gaps(self) -> List[Dict[str, Any]]:
        """
        Find critical and high-priority capability gaps.

        Returns:
            List of critical gaps requiring immediate attention
        """
        results = []

        try:
            # Get gaps from CapabilityGapDetail
            critical_gaps = CapabilityGapDetail.query.filter(
                CapabilityGapDetail.gap_severity.in_(["critical", "high"])
            ).all()

            for gap in critical_gaps:
                capability = gap.capability

                results.append(
                    {
                        "gap_id": gap.id,
                        "capability_id": gap.capability_id,
                        "capability_name": capability.name if capability else "Unknown",
                        "gap_severity": gap.gap_severity,
                        "coverage_status": gap.coverage_status,
                        "coverage_percentage": gap.coverage_percentage,
                        "gap_description": gap.gap_description,
                        "business_impact": gap.business_impact,
                        "business_impact_score": gap.business_impact_score,
                        "priority_score": gap.priority_score,
                        "urgency_level": gap.urgency_level,
                        "solution_type": gap.solution_type,
                        "estimated_cost": gap.estimated_cost,
                        "quantified_value": gap.quantified_value,
                    }
                )

            # Also find critical capabilities with no coverage
            critical_capabilities = UnifiedCapability.query.filter(
                or_(
                    UnifiedCapability.strategic_importance == "critical",
                    UnifiedCapability.business_criticality == "mission_critical",
                )
            ).all()

            # Batch prefetch mapping counts for critical capabilities to avoid N+1
            critical_cap_ids = [c.id for c in critical_capabilities]
            critical_mapping_counts = db.session.query(
                UnifiedApplicationCapabilityMapping.unified_capability_id,
                func.count(UnifiedApplicationCapabilityMapping.id)
            ).filter(
                UnifiedApplicationCapabilityMapping.unified_capability_id.in_(critical_cap_ids),
                UnifiedApplicationCapabilityMapping.is_active == True
            ).group_by(
                UnifiedApplicationCapabilityMapping.unified_capability_id
            ).all() if critical_cap_ids else []
            critical_count_map = dict(critical_mapping_counts)

            for capability in critical_capabilities:
                mapping_count = critical_count_map.get(capability.id, 0)

                if mapping_count == 0:
                    results.append(
                        {
                            "gap_id": f"strategic_{capability.id}",
                            "capability_id": capability.id,
                            "capability_name": capability.name,
                            "gap_severity": "critical",
                            "coverage_status": "gap",
                            "coverage_percentage": 0,
                            "gap_description": f"Critical capability '{capability.name}' has no application support",
                            "business_impact": "Strategic capability without technology enablement",
                            "business_impact_score": 10,
                            "priority_score": 10,
                            "urgency_level": "immediate",
                            "solution_type": "new_application",
                            "estimated_cost": None,
                            "quantified_value": None,
                        }
                    )

            # Sort by priority score
            results.sort(key=lambda x: -(x.get("priority_score") or 0))

        except Exception as e:
            logger.error(f"Error finding critical gaps: {e}")

        return results

    def find_vendor_lifecycle_risks(self) -> List[Dict[str, Any]]:
        """
        Find vendor products with lifecycle risks (end-of-life, sunset, deprecated).

        Returns:
            List of vendor products with lifecycle concerns
        """
        results = []

        try:
            from app.models.vendor.vendor_product import VendorProductDetail

            # Check for EOL dates in the near future (18 months)
            cutoff_date = datetime.utcnow() + timedelta(days=548)  # ~18 months

            # Query vendor products with EOL concerns
            products = VendorProductDetail.query.filter(
                or_(
                    VendorProductDetail.end_of_support_date <= cutoff_date,
                    VendorProductDetail.status == "deprecated",
                    VendorProductDetail.availability_status.in_(
                        ["deprecated", "sunset", "end_of_life"]
                    ),
                )
            ).all()

            for product in products:
                # Find capabilities affected by this product
                affected_capabilities = self._find_capabilities_using_product(product.id)

                days_until_eol = None
                if product.end_of_support_date:
                    eol_date = product.end_of_support_date
                    if hasattr(eol_date, 'date'):
                        eol_date = eol_date.date()
                    delta = eol_date - datetime.utcnow().date()
                    days_until_eol = delta.days

                results.append(
                    {
                        "product_id": product.id,
                        "product_name": product.product_name,
                        "product_code": product.product_code,
                        "current_version": product.current_version,
                        "status": product.status,
                        "end_of_support_date": product.end_of_support_date.isoformat()
                        if product.end_of_support_date
                        else None,
                        "days_until_eol": days_until_eol,
                        "affected_capability_count": len(affected_capabilities),
                        "affected_capabilities": affected_capabilities[:5],  # Top 5
                        "risk_level": self._calculate_eol_risk(
                            days_until_eol, len(affected_capabilities)
                        ),
                        "recommendation": self._get_eol_recommendation(product, days_until_eol),
                    }
                )

            # Sort by risk level and days until EOL
            results.sort(
                key=lambda x: (
                    -self._risk_score(x["risk_level"]),
                    x["days_until_eol"] if x["days_until_eol"] is not None else 9999,
                )
            )

        except Exception as e:
            logger.error(f"Error finding vendor lifecycle risks: {e}")

        return results

    def find_uncovered_capabilities(self) -> List[Dict[str, Any]]:
        """
        Find capabilities with no application coverage at all.

        Returns:
            List of uncovered capabilities
        """
        results = []

        try:
            # Use a subquery to find uncovered capabilities in a single query
            # instead of N+1 count queries per capability
            mapped_cap_ids = db.session.query(
                UnifiedApplicationCapabilityMapping.unified_capability_id
            ).filter(
                UnifiedApplicationCapabilityMapping.is_active == True  # noqa: E712
            ).distinct()

            uncovered_capabilities = UnifiedCapability.query.filter(
                ~UnifiedCapability.id.in_(mapped_cap_ids)
            ).all()

            for capability in uncovered_capabilities:
                results.append(
                    {
                        "capability_id": capability.id,
                        "capability_name": capability.name,
                        "capability_code": capability.code,
                        "level": capability.level,
                        "domain_id": capability.domain_id,
                        "strategic_importance": capability.strategic_importance,
                        "business_criticality": capability.business_criticality,
                        "coverage_percentage": 0,
                        "gap_severity": self._calculate_no_coverage_severity(capability),
                        "recommendation": "No applications support this capability. Assess build vs buy options.",
                    }
                )

            # Sort by severity
            results.sort(key=lambda x: -self._severity_score(x["gap_severity"]))

        except Exception as e:
            logger.error(f"Error finding uncovered capabilities: {e}")

        return results

    def find_maturity_gaps(self, organization_id: int) -> Dict[str, Any]:
        """
        Read the recorded maturity gap for the caller's own capabilities.

        Every ``current``, ``target`` and ``target_gap`` value is copied from
        ``CapabilityHeatmapService``'s batched per-capability maturity read
        (ADR-MAT-1); this method computes no maturity level and no
        comparison of its own.
        A capability the tenant never assessed, or assessed with no recorded
        target, is never a gap -- it is listed in ``unassessed`` with the
        reason instead.

        Returns:
            ``{"gaps": [...], "unassessed": [...], "maturity_source":
            "unified_capabilities", "reason": None}``. With
            ``organization_id`` unresolved, nothing is read: ``{"gaps":
            None, "unassessed": None, "maturity_source":
            "unified_capabilities", "reason": "no_tenant_context"}``.
        """
        if organization_id is None:
            return {
                "gaps": None,
                "unassessed": None,
                "maturity_source": "unified_capabilities",
                "reason": "no_tenant_context",
            }

        from app.modules.capabilities.services.capability_heatmap_service import (
            CapabilityHeatmapService,
        )

        capability_ids = [
            row[0]
            for row in db.session.query(UnifiedCapability.id)
            .filter(UnifiedCapability.organization_id == organization_id)
            .all()
        ]
        blocks = CapabilityHeatmapService().maturity_for_capability_ids(
            capability_ids, organization_id=organization_id
        )

        gaps: List[Dict[str, Any]] = []
        unassessed: List[Dict[str, Any]] = []
        for capability_id in sorted(blocks.keys()):
            block = blocks[capability_id]
            if block["under_target"] is True:
                gaps.append(
                    {
                        "gap_type": "capability_maturity",
                        "capability_id": block["capability_id"],
                        "element_id": block["element_id"],
                        "current": block["current"],
                        "target": block["target"],
                        "target_gap": block["target_gap"],
                        "maturity_source": block["maturity_source"],
                    }
                )
            elif block["under_target"] is None:
                unassessed.append(
                    {
                        "capability_id": block["capability_id"],
                        "element_id": block["element_id"],
                        "reason": block["reason"],
                    }
                )
            # under_target is False: recorded, at or above target -- not a
            # gap and not listed as unassessed either.

        return {
            "gaps": gaps,
            "unassessed": unassessed,
            "maturity_source": "unified_capabilities",
            "reason": None,
        }

    def get_comprehensive_gap_summary(self) -> Dict[str, Any]:
        """
        Get a comprehensive summary of all gap types.

        Returns:
            Dictionary with summary statistics for all gap dimensions
        """
        return {
            "low_coverage_count": len(self.find_low_coverage_capabilities(threshold=50)),
            "no_coverage_count": len(self.find_uncovered_capabilities()),
            "rationalization_opportunities": len(self.find_rationalization_opportunities()),
            "legacy_only_capabilities": len(self.find_capabilities_with_only_legacy_apps()),
            "critical_gaps": len(self.find_critical_gaps()),
            "vendor_lifecycle_risks": len(self.find_vendor_lifecycle_risks()),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _generate_recommendations(
        self, query_type: str, results: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Generate AI-powered recommendations based on query results."""
        recommendations = []

        if not results:
            return [
                {
                    "priority": "info",
                    "category": "general",
                    "title": "No gaps found",
                    "description": "No significant gaps were identified for this query.",
                    "action": None,
                }
            ]

        count = len(results) if isinstance(results, list) else 0

        if query_type == "low_coverage":
            recommendations.append(
                {
                    "priority": "high" if count > 10 else "medium",
                    "category": "coverage",
                    "title": f"Address {count} low coverage capabilities",
                    "description": "Consider investing in applications or enhancing existing ones to improve capability coverage.",
                    "action": "Review capability mapping and identify quick wins",
                }
            )

        elif query_type == "rationalization":
            potential_savings = sum(r.get("estimated_annual_savings", 0) for r in results)
            recommendations.append(
                {
                    "priority": "high" if potential_savings > 100000 else "medium",
                    "category": "cost_optimization",
                    "title": f"Rationalize {count} capability overlaps",
                    "description": f"Estimated annual savings potential: ${potential_savings:,.0f}",
                    "action": "Prioritize rationalization projects by ROI",
                }
            )

        elif query_type == "legacy_only":
            recommendations.append(
                {
                    "priority": "high",
                    "category": "modernization",
                    "title": f"Modernize {count} legacy-dependent capabilities",
                    "description": "These capabilities rely solely on legacy applications and pose operational risk.",
                    "action": "Create modernization roadmap with phased approach",
                }
            )

        elif query_type == "vendor_lifecycle":
            recommendations.append(
                {
                    "priority": "critical",
                    "category": "risk_mitigation",
                    "title": f"Address {count} vendor lifecycle risks",
                    "description": "Products approaching end-of-life require migration planning.",
                    "action": "Initiate vendor replacement analysis immediately",
                }
            )

        return recommendations

    # Helper methods
    def _calculate_gap_severity(
        self, coverage: float, strategic_importance: str, criticality: str
    ) -> str:
        """Calculate gap severity based on coverage and business importance."""
        if coverage == 0:
            if criticality == "mission_critical" or strategic_importance == "critical":
                return "critical"
            return "high"
        elif coverage < 25:
            if criticality in ["mission_critical", "important"]:
                return "critical"
            return "high"
        elif coverage < 50:
            if strategic_importance == "critical":
                return "high"
            return "medium"
        else:
            return "low"

    def _calculate_rationalization_priority(self, app_count: int, strategic_importance: str) -> str:
        """Calculate rationalization priority."""
        if app_count >= 5:
            return "critical"
        elif app_count >= 3:
            if strategic_importance in ["low", "medium"]:
                return "high"
            return "medium"
        else:
            return "low"

    def _calculate_modernization_urgency(
        self, capability: UnifiedCapability, legacy_apps: List
    ) -> str:
        """Calculate modernization urgency based on capability importance and app age."""
        if capability.business_criticality == "mission_critical":
            return "immediate"
        elif capability.strategic_importance == "critical":
            return "high"
        elif len(legacy_apps) >= 3:
            return "high"
        elif capability.strategic_importance in ["high", "medium"]:
            return "medium"
        return "low"

    def _calculate_no_coverage_severity(self, capability: UnifiedCapability) -> str:
        """Calculate severity for uncovered capabilities."""
        if capability.business_criticality == "mission_critical":
            return "critical"
        elif capability.strategic_importance == "critical":
            return "critical"
        elif capability.strategic_importance == "high":
            return "high"
        elif capability.level == 1:  # Strategic level
            return "high"
        return "medium"

    def _calculate_eol_risk(self, days_until_eol: Optional[int], affected_count: int) -> str:
        """Calculate risk level for EOL products."""
        if days_until_eol is None:
            return "medium"
        if days_until_eol <= 90:
            return "critical"
        elif days_until_eol <= 180:
            if affected_count >= 3:
                return "critical"
            return "high"
        elif days_until_eol <= 365:
            return "high" if affected_count >= 5 else "medium"
        return "low"

    def _is_legacy_application(self, app: ApplicationComponent) -> bool:
        """Determine if an application is considered legacy."""
        legacy_indicators = [
            (app.lifecycle_status or "") in ["retired", "phase_out", "sunset", "legacy"],
            getattr(app, "technology_age_years", 0) and getattr(app, "technology_age_years", 0) > 10,  # model-safety-ok: field is technology_age_years on ApplicationComponent
            getattr(app, "lifecycle_status", "") in ["deprecated", "legacy", "retiring"],  # model-safety-ok: field is lifecycle_status on ApplicationComponent
        ]
        return any(legacy_indicators)

    def _estimate_rationalization_savings(self, apps: List[Dict]) -> float:
        """Estimate potential annual savings from rationalization."""
        # Simplified calculation: assume $50k per redundant app in maintenance/licensing
        base_savings_per_app = BASE_SAVINGS_PER_APP_USD
        return max(0, (len(apps) - 1) * base_savings_per_app)

    def _find_capabilities_using_product(self, product_id: int) -> List[Dict]:
        """Find capabilities that use a specific vendor product."""
        # This would need to be implemented based on the actual product-capability mapping
        return []

    def _get_coverage_recommendation(self, coverage: float, capability: UnifiedCapability) -> str:
        """Generate coverage improvement recommendation."""
        if coverage == 0:
            return "Critical: No application coverage. Assess build vs buy options immediately."
        elif coverage < 25:
            return "High priority: Enhance existing applications or acquire new solution."
        elif coverage < 50:
            return "Medium priority: Review coverage gaps and create improvement plan."
        return "Low priority: Minor coverage improvements possible."

    def _get_eol_recommendation(self, product, days_until_eol: Optional[int]) -> str:
        """Generate EOL-related recommendation."""
        if days_until_eol is None:
            return "Verify end-of-life date with vendor and plan migration."
        elif days_until_eol <= 90:
            return "URGENT: Immediate migration required. EOL within 3 months."
        elif days_until_eol <= 180:
            return "HIGH: Accelerate migration planning. EOL within 6 months."
        elif days_until_eol <= 365:
            return "MEDIUM: Begin vendor evaluation for replacement."
        return "LOW: Monitor vendor roadmap and plan for future migration."

    def _severity_score(self, severity: str) -> int:
        """Convert severity to numeric score."""
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 0)

    def _priority_score(self, priority: str) -> int:
        """Convert priority to numeric score."""
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(priority, 0)

    def _urgency_score(self, urgency: str) -> int:
        """Convert urgency to numeric score."""
        return {"immediate": 4, "high": 3, "medium": 2, "low": 1}.get(urgency, 0)

    def _risk_score(self, risk: str) -> int:
        """Convert risk level to numeric score."""
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(risk, 0)
