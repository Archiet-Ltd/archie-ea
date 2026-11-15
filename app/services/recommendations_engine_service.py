"""
Actionable Recommendations Engine

Provides proactive alerts, risk-based prioritization, investment optimization,
and technical debt remediation recommendations based on enterprise architecture data.

Features:
- Automated health scanning across all domains
- Risk-based prioritization with impact scores
- Investment optimization recommendations
- Technical debt identification and remediation paths
- Persona-specific recommendations
- Trend analysis and predictions
"""

import logging
from collections import defaultdict
from datetime import datetime  # dead-code-ok: used in contract expiry check
from typing import Any, Dict, List

from sqlalchemy import func, or_  # dead-code-ok: used in scan methods

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.vendor.vendor_organization import VendorOrganization  # dead-code-ok: used in vendor scanning

logger = logging.getLogger(__name__)


class RecommendationsEngineService:
    """
    Enterprise Architecture Recommendations Engine.
    Analyzes data to provide actionable recommendations.
    """

    # Risk weights for different factors
    RISK_WEIGHTS = {
        "no_owner": 25,
        "no_dr": 30,
        "outdated_technology": 20,
        "vendor_expiring": 25,
        "single_point_of_failure": 35,
        "low_maturity": 15,
        "high_technical_debt": 25,
        "compliance_gap": 30,
        "no_documentation": 10,
        "legacy_status": 20,
    }

    # Priority levels
    PRIORITY_LEVELS = {
        "critical": {"min_score": 80, "color": "red", "icon": "alert-triangle"},
        "high": {"min_score": 60, "color": "orange", "icon": "alert-circle"},
        "medium": {"min_score": 40, "color": "yellow", "icon": "info"},
        "low": {"min_score": 0, "color": "blue", "icon": "lightbulb"},
    }

    # Persona-specific focus areas
    PERSONA_FOCUS = {
        "enterprise_architect": ["strategic_alignment", "portfolio_health", "governance"],
        "solutions_architect": ["integration_patterns", "vendor_products", "scalability"],
        "application_architect": ["technical_debt", "modernization", "api_coverage"],
        "integration_architect": ["data_flows", "interfaces", "redundancy"],
        "systems_architect": ["infrastructure", "dr_coverage", "security"],
        "business_architect": ["capability_gaps", "maturity", "value_streams"],
        "business_analyst": ["requirements", "process_coverage", "stakeholder_impact"],
        "product_analyst": ["feature_gaps", "customer_journeys", "roadmap_alignment"],
        "cio": ["portfolio_health", "risk_summary", "investment_roi", "compliance"],
    }

    def __init__(self):
        """Initialize the Recommendations Engine."""
        self.scan_timestamp = None
        self.cached_recommendations = None

    def get_all_recommendations(self, persona: str = None, refresh: bool = False) -> Dict[str, Any]:
        """
        Get all recommendations, optionally filtered by persona.

        Args:
            persona: Optional persona to filter recommendations
            refresh: Force refresh of cached recommendations

        Returns:
            Dict with categorized recommendations
        """
        try:
            if self.cached_recommendations and not refresh:
                # Check if cache is still valid (5 minutes)
                if (
                    self.scan_timestamp
                    and (datetime.utcnow() - self.scan_timestamp).total_seconds() < 300
                ):
                    return self._filter_by_persona(self.cached_recommendations, persona)

            # Run full scan
            recommendations = self._run_full_scan()
            self.cached_recommendations = recommendations
            self.scan_timestamp = datetime.utcnow()

            return self._filter_by_persona(recommendations, persona)

        except Exception as e:
            logger.error(f"Error getting recommendations: {e}", exc_info=True)
            return {"error": str(e), "alerts": [], "recommendations": [], "summary": {"total": 0}}

    def _run_full_scan(self) -> Dict[str, Any]:
        """Run a full scan of all domains and generate recommendations."""
        all_alerts = []
        all_recommendations = []

        # Scan applications
        app_alerts, app_recs = self._scan_applications()
        all_alerts.extend(app_alerts)
        all_recommendations.extend(app_recs)

        # Scan capabilities
        cap_alerts, cap_recs = self._scan_capabilities()
        all_alerts.extend(cap_alerts)
        all_recommendations.extend(cap_recs)

        # Scan vendors
        vendor_alerts, vendor_recs = self._scan_vendors()
        all_alerts.extend(vendor_alerts)
        all_recommendations.extend(vendor_recs)

        # Cross-domain analysis
        cross_alerts, cross_recs = self._cross_domain_analysis()
        all_alerts.extend(cross_alerts)
        all_recommendations.extend(cross_recs)

        # Sort by priority score
        all_alerts.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
        all_recommendations.sort(key=lambda x: x.get("impact_score", 0), reverse=True)

        # Generate summary
        summary = self._generate_summary(all_alerts, all_recommendations)

        return {
            "alerts": all_alerts,
            "recommendations": all_recommendations,
            "summary": summary,
            "scan_timestamp": datetime.utcnow().isoformat(),
            "health_score": self._calculate_overall_health(all_alerts),
        }

    def _scan_applications(self) -> tuple:
        """Scan applications for issues and recommendations."""
        alerts = []
        recommendations = []

        try:
            applications = ApplicationComponent.query.all()

            # Track statistics
            no_owner_count = 0
            no_dr_count = 0
            legacy_count = 0
            high_criticality_issues = []

            for app in applications:
                app_issues = []

                # Check for missing business owner
                if not getattr(app, "business_owner", None):
                    no_owner_count += 1
                    app_issues.append("no_owner")

                # Check for missing DR
                if not getattr(app, "dr_status", None) or getattr(app, "dr_status", "") == "None":
                    no_dr_count += 1
                    app_issues.append("no_dr")

                # Check for legacy status
                # ApplicationComponent uses 'lifecycle_status' for status information
                status = (getattr(app, "lifecycle_status") or "").lower()
                if "legacy" in status or "deprecated" in status or "sunset" in status:
                    legacy_count += 1
                    app_issues.append("legacy_status")

                # High criticality with issues
                criticality = (getattr(app, "criticality") or "").lower()
                if criticality in ["high", "critical"] and app_issues:
                    priority_score = sum(self.RISK_WEIGHTS.get(issue, 10) for issue in app_issues)
                    high_criticality_issues.append(
                        {"app": app, "issues": app_issues, "priority_score": priority_score}
                    )

            # Generate alerts for applications without owners
            if no_owner_count > 0:
                alerts.append(
                    {
                        "id": "app_no_owner",
                        "type": "application",
                        "category": "governance",
                        "title": f"{no_owner_count} applications without business owner",
                        "description": "Applications without assigned business owners lack accountability and may have unclear requirements.",
                        "priority": self._get_priority_level(no_owner_count * 5),
                        "priority_score": no_owner_count * 5,
                        "count": no_owner_count,
                        "action": "Assign business owners to all applications",
                        "action_url": "/applications/",
                        "action_label": "Open Application List",
                        "impact": "governance",
                        "effort": "low",
                        "query": "Show applications without business owner",
                    }
                )

            # Generate alerts for DR gaps
            if no_dr_count > 0:
                alerts.append(
                    {
                        "id": "app_no_dr",
                        "type": "application",
                        "category": "resilience",
                        "title": f"{no_dr_count} applications need DR review",
                        "description": "Applications without disaster recovery plans pose business continuity risks.",
                        "priority": self._get_priority_level(no_dr_count * 8),
                        "priority_score": no_dr_count * 8,
                        "count": no_dr_count,
                        "action": "Assess and implement DR plans",
                        "action_url": "/applications/",
                        "action_label": "Open Application List",
                        "impact": "high",
                        "effort": "high",
                        "query": "Show applications without DR plan",
                    }
                )

            # Generate alerts for legacy systems
            if legacy_count > 0:
                alerts.append(
                    {
                        "id": "app_legacy",
                        "type": "application",
                        "category": "modernization",
                        "title": f"{legacy_count} legacy applications require attention",
                        "description": "Legacy applications may pose security, maintenance, and integration challenges.",
                        "priority": self._get_priority_level(legacy_count * 6),
                        "priority_score": legacy_count * 6,
                        "count": legacy_count,
                        "action": "Develop modernization roadmap",
                        "action_url": "/applications/",
                        "action_label": "Open Application List",
                        "impact": "strategic",
                        "effort": "high",
                        "query": "Show legacy applications",
                    }
                )

            # High criticality issues
            for item in high_criticality_issues[:5]:  # Top 5
                app = item["app"]
                alerts.append(
                    {
                        "id": f"app_critical_{app.id}",
                        "type": "application",
                        "category": "risk",
                        "title": f'Critical application "{app.name}" has {len(item["issues"])} issues',
                        "description": f'Issues: {", ".join(item["issues"])}',
                        "priority": self._get_priority_level(item["priority_score"]),
                        "priority_score": item["priority_score"],
                        "entity_id": app.id,
                        "entity_name": app.name,
                        "action": "Address issues for critical application",
                        "action_url": f"/applications/{app.id}",
                        "action_label": f"Open {app.name}",
                        "impact": "critical",
                        "effort": "medium",
                    }
                )

            # Recommendations
            if no_owner_count > 5:
                recommendations.append(
                    {
                        "id": "rec_ownership_program",
                        "type": "application",
                        "category": "governance",
                        "title": "Implement Application Ownership Program",
                        "description": f"With {no_owner_count} applications lacking owners, establish a formal ownership assignment program.",
                        "impact_score": 75,
                        "effort": "medium",
                        "timeline": "1 - 2 months",
                        "benefits": [
                            "Improved accountability",
                            "Clearer decision making",
                            "Better requirements management",
                        ],
                        "steps": [
                            "Define ownership roles and responsibilities",
                            "Create ownership assignment workflow",
                            "Assign owners to critical applications first",
                            "Implement regular ownership review process",
                        ],
                    }
                )

            if legacy_count > 3:
                recommendations.append(
                    {
                        "id": "rec_modernization_roadmap",
                        "type": "application",
                        "category": "modernization",
                        "title": "Develop Application Modernization Roadmap",
                        "description": f"{legacy_count} legacy applications need modernization planning.",
                        "impact_score": 80,
                        "effort": "high",
                        "timeline": "3 - 6 months",
                        "benefits": [
                            "Reduced technical debt",
                            "Improved security",
                            "Better integration capabilities",
                        ],
                        "steps": [
                            "Assess each legacy application",
                            "Determine modernization approach (replatform, refactor, replace)",
                            "Prioritize based on business value and risk",
                            "Create phased implementation plan",
                        ],
                    }
                )

        except Exception as e:
            logger.error(f"Error scanning applications: {e}")

        return alerts, recommendations

    def _scan_capabilities(self) -> tuple:
        """Scan capabilities for issues and recommendations."""
        alerts = []
        recommendations = []

        try:
            capabilities = BusinessCapability.query.all()

            low_maturity_count = 0
            no_automation_count = 0

            for cap in capabilities:
                # Check maturity level
                maturity = getattr(cap, "maturity_level", None)
                if maturity is not None and maturity < 3:
                    low_maturity_count += 1

                # Check automation level
                automation = getattr(cap, "automation_level", None)
                if not automation or automation == "None" or automation == "Manual":
                    no_automation_count += 1

            # Alerts for low maturity
            if low_maturity_count > 0:
                alerts.append(
                    {
                        "id": "cap_low_maturity",
                        "type": "capability",
                        "category": "maturity",
                        "title": f"{low_maturity_count} capabilities with low maturity",
                        "description": "Capabilities with maturity below 3 may not be meeting business needs effectively.",
                        "priority": self._get_priority_level(low_maturity_count * 4),
                        "priority_score": low_maturity_count * 4,
                        "count": low_maturity_count,
                        "action": "Develop capability improvement plans",
                        "action_url": "/strategic/capability-health",
                        "action_label": "Open Capabilities",
                        "impact": "business",
                        "effort": "medium",
                        "query": "Show capabilities with maturity below 3",
                    }
                )

            # Alerts for no automation
            if no_automation_count > 0:
                alerts.append(
                    {
                        "id": "cap_no_automation",
                        "type": "capability",
                        "category": "automation",
                        "title": f"{no_automation_count} capabilities lack automation",
                        "description": "Manual capabilities may be inefficient and error-prone.",
                        "priority": self._get_priority_level(no_automation_count * 3),
                        "priority_score": no_automation_count * 3,
                        "count": no_automation_count,
                        "action": "Identify automation opportunities",
                        "action_url": "/strategic/capability-health",
                        "action_label": "Open Capabilities",
                        "impact": "efficiency",
                        "effort": "high",
                        "query": "Show capabilities without automation",
                    }
                )

            # Recommendations
            if low_maturity_count > 5:
                recommendations.append(
                    {
                        "id": "rec_maturity_program",
                        "type": "capability",
                        "category": "maturity",
                        "title": "Launch Capability Maturity Improvement Program",
                        "description": f"Address {low_maturity_count} low-maturity capabilities systematically.",
                        "impact_score": 70,
                        "effort": "high",
                        "timeline": "6 - 12 months",
                        "benefits": [
                            "Improved business outcomes",
                            "Better resource utilization",
                            "Competitive advantage",
                        ],
                        "steps": [
                            "Prioritize capabilities by business impact",
                            "Define target maturity levels",
                            "Create improvement roadmaps",
                            "Implement monitoring and tracking",
                        ],
                    }
                )

        except Exception as e:
            logger.error(f"Error scanning capabilities: {e}")

        return alerts, recommendations

    def _scan_vendors(self) -> tuple:
        """Scan vendors for issues and recommendations."""
        alerts = []
        recommendations = []

        try:
            vendors = VendorOrganization.query.all()

            expiring_soon = 0
            high_risk_count = 0
            no_tier_count = 0

            for vendor in vendors:
                # Check contract expiration
                contract_end = getattr(vendor, "contract_end_date", None)  # model-safety-ok: optional field
                if contract_end:
                    try:
                        from app.services.vendor_register import days_until, renewal_is_due

                        end_day = contract_end.date() if isinstance(contract_end, datetime) else contract_end
                        days_until_expiry = days_until(end_day)
                        if days_until_expiry > 0 and renewal_is_due(days_until_expiry):
                            expiring_soon += 1
                    except (TypeError, AttributeError) as e:
                        logger.warning(
                            f"Error calculating contract expiry for vendor {getattr(vendor, 'name', 'Unknown')}: {e}"
                        )

                # Check risk level - VendorOrganization has no direct risk_level, using financial_health_score or acquisition_risk as proxy
                # Or check if there's a different risk field.
                # Model shows: acquisition_risk, vendor_lock_in_risk.
                # Let's use acquisition_risk as the primary risk indicator for now.
                risk = (getattr(vendor, "acquisition_risk") or "").lower()
                if risk in ["high", "critical"]:
                    high_risk_count += 1

                # Check strategic tier
                tier = getattr(vendor, "strategic_tier", None)  # model-safety-ok: optional field
                if not tier:
                    no_tier_count += 1

            # Alerts for expiring contracts
            if expiring_soon > 0:
                alerts.append(
                    {
                        "id": "vendor_expiring",
                        "type": "vendor",
                        "category": "contract",
                        "title": f"{expiring_soon} vendor contracts expiring within 90 days",
                        "description": "Contracts nearing expiration need renewal decisions.",
                        "priority": self._get_priority_level(expiring_soon * 10),
                        "priority_score": expiring_soon * 10,
                        "count": expiring_soon,
                        "action": "Review and renew contracts",
                        "impact": "operational",
                        "effort": "medium",
                        "query": "List vendors expiring in 90 days",
                    }
                )

            # Alerts for high risk vendors
            if high_risk_count > 0:
                alerts.append(
                    {
                        "id": "vendor_high_risk",
                        "type": "vendor",
                        "category": "risk",
                        "title": f"{high_risk_count} high-risk vendors require attention",
                        "description": "High-risk vendor relationships may impact business continuity.",
                        "priority": self._get_priority_level(high_risk_count * 12),
                        "priority_score": high_risk_count * 12,
                        "count": high_risk_count,
                        "action": "Develop risk mitigation plans",
                        "impact": "risk",
                        "effort": "high",
                        "query": "Show high risk vendors",
                    }
                )

            # Recommendations
            if expiring_soon >= 3:
                recommendations.append(
                    {
                        "id": "rec_contract_review",
                        "type": "vendor",
                        "category": "contract",
                        "title": "Conduct Vendor Contract Review Sprint",
                        "description": f"{expiring_soon} contracts expiring soon require coordinated review.",
                        "impact_score": 85,
                        "effort": "medium",
                        "timeline": "2 - 4 weeks",
                        "benefits": [
                            "Avoid service disruption",
                            "Negotiate better terms",
                            "Consolidate vendors",
                        ],
                        "steps": [
                            "List all expiring contracts with details",
                            "Assess vendor performance and alternatives",
                            "Prepare negotiation strategy",
                            "Execute renewals or transitions",
                        ],
                    }
                )

        except Exception as e:
            logger.error(f"Error scanning vendors: {e}")

        return alerts, recommendations

    def _cross_domain_analysis(self) -> tuple:
        """Perform cross-domain analysis for broader insights."""
        alerts = []
        recommendations = []

        try:
            # Check for capability gaps (capabilities without supporting applications)
            capabilities = BusinessCapability.query.all()
            applications = ApplicationComponent.query.all()

            # Build a map of application names for matching
            app_names_lower = {app.name.lower(): app for app in applications if app.name}

            # Analyze capability-application coverage
            unsupported_capabilities = []
            for cap in capabilities:
                # Check if any application name contains capability-related keywords
                cap_name_lower = cap.name.lower() if cap.name else ""
                has_support = False

                # Simple heuristic: check if any app name contains capability keywords
                cap_keywords = cap_name_lower.replace("_", " ").replace("-", " ").split()
                for app_name in app_names_lower:
                    if any(keyword in app_name for keyword in cap_keywords if len(keyword) > 3):
                        has_support = True
                        break

                # Also check if capability has direct application relationships
                if hasattr(cap, "applications") and cap.applications:
                    has_support = True
                elif hasattr(cap, "application_mappings") and cap.application_mappings:
                    has_support = True

                if not has_support:
                    unsupported_capabilities.append(cap)

            # Generate alerts for unsupported capabilities
            if len(unsupported_capabilities) > 0:
                critical_unsupported = [
                    c
                    for c in unsupported_capabilities
                    if getattr(c, "level", 0) == 1
                    or str(getattr(c, "criticality", "")).lower() in ["high", "critical"]
                ]

                if critical_unsupported:
                    alerts.append(
                        {
                            "id": "cap_no_app_support_critical",
                            "type": "cross_domain",
                            "category": "strategic_alignment",
                            "title": f"{len(critical_unsupported)} critical capabilities lack application support",
                            "description": "High-level or critical capabilities without supporting applications may indicate strategic gaps.",
                            "priority": self._get_priority_level(len(critical_unsupported) * 15),
                            "priority_score": len(critical_unsupported) * 15,
                            "count": len(critical_unsupported),
                            "action": "Review and map applications to critical capabilities",
                            "impact": "strategic",
                            "effort": "high",
                            "query": "Show capabilities without application support",
                        }
                    )

                if len(unsupported_capabilities) > 5:
                    alerts.append(
                        {
                            "id": "cap_no_app_support",
                            "type": "cross_domain",
                            "category": "portfolio_health",
                            "title": f"{len(unsupported_capabilities)} capabilities lack clear application support",
                            "description": "Capabilities without mapped applications may have undocumented dependencies.",
                            "priority": self._get_priority_level(len(unsupported_capabilities) * 3),
                            "priority_score": len(unsupported_capabilities) * 3,
                            "count": len(unsupported_capabilities),
                            "action": "Conduct capability-application mapping exercise",
                            "impact": "governance",
                            "effort": "medium",
                            "query": "Show capabilities without applications",
                        }
                    )

            # Check for vendor concentration risk (ARCH-016).
            #
            # A concentration finding is a statement about the *distribution* of a
            # non-empty set. It requires a real population to be meaningful, so this
            # only fires above a minimum sample size (MIN_VENDORS_FOR_CONCENTRATION),
            # and zero-strategic-tier is treated as missing data quality, not a risk
            # finding — "0 of N vendors are strategic" is an onboarding/data-quality
            # gap (nobody has tagged tiers yet), not evidence of concentration.
            MIN_VENDORS_FOR_CONCENTRATION = 10
            vendors = VendorOrganization.query.all()
            strategic_vendors = [
                v
                for v in vendors
                if getattr(v, "strategic_tier", "") in ["1", "Tier 1", "Strategic"]
            ]
            if len(vendors) >= MIN_VENDORS_FOR_CONCENTRATION:
                if len(strategic_vendors) == 0:
                    alerts.append(
                        {
                            "id": "vendor_tier_data_missing",
                            "type": "cross_domain",
                            "category": "data_quality",
                            "title": "Vendor strategic tiers not set",
                            "description": (
                                f"None of {len(vendors)} vendors have a strategic tier assigned, "
                                "so concentration risk cannot be assessed yet."
                            ),
                            "priority": "low",
                            "priority_score": 15,
                            "action": "Assign strategic tiers to vendors to enable concentration analysis",
                            "impact": "data_quality",
                            "effort": "low",
                        }
                    )
                elif len(strategic_vendors) < 3:
                    alerts.append(
                        {
                            "id": "vendor_concentration",
                            "type": "cross_domain",
                            "category": "risk",
                            "title": "Vendor concentration risk detected",
                            "description": f"Only {len(strategic_vendors)} strategic vendors identified. Consider diversifying vendor portfolio.",
                            "priority": "medium",
                            "priority_score": 35,
                            "action": "Review vendor portfolio for strategic diversity",
                            "impact": "risk",
                            "effort": "medium",
                        }
                    )

            # Standard portfolio review recommendation
            recommendations.append(
                {
                    "id": "rec_portfolio_review",
                    "type": "cross_domain",
                    "category": "strategic",
                    "title": "Conduct Quarterly Portfolio Review",
                    "description": "Regular portfolio reviews ensure alignment between applications, capabilities, and business strategy.",
                    "impact_score": 65,
                    "effort": "medium",
                    "timeline": "Recurring quarterly",
                    "benefits": [
                        "Strategic alignment",
                        "Identify redundancy",
                        "Optimize investments",
                    ],
                    "steps": [
                        "Gather portfolio metrics",
                        "Review capability coverage",
                        "Assess technology health",
                        "Update roadmaps as needed",
                    ],
                }
            )

            # Capability mapping recommendation if gaps found
            if len(unsupported_capabilities) > 3:
                recommendations.append(
                    {
                        "id": "rec_capability_mapping",
                        "type": "cross_domain",
                        "category": "governance",
                        "title": "Establish Capability-Application Mapping",
                        "description": f"{len(unsupported_capabilities)} capabilities lack clear application support. Establish formal mapping.",
                        "impact_score": 70,
                        "effort": "medium",
                        "timeline": "1 - 2 months",
                        "benefits": [
                            "Clear accountability",
                            "Better investment decisions",
                            "Reduced duplication",
                        ],
                        "steps": [
                            "Identify unmapped capabilities",
                            "Interview application owners",
                            "Document capability-application relationships",
                            "Establish ongoing governance process",
                        ],
                    }
                )

        except Exception as e:
            logger.error(f"Error in cross-domain analysis: {e}", exc_info=True)

        return alerts, recommendations

    def _get_priority_level(self, score: int) -> str:
        """Get priority level based on score."""
        for level, config in self.PRIORITY_LEVELS.items():
            if score >= config["min_score"]:
                return level
        return "low"

    def _calculate_overall_health(self, alerts: List[Dict]) -> int:
        """Calculate overall portfolio health score (0-100) from real data.

        Uses capability coverage as the primary health signal, with alert
        severity as a secondary penalty. This produces meaningful scores
        (not always 0 or 100) that reflect actual portfolio state.
        """
        if not alerts:
            return 95  # No issues = high health

        # Primary signal: capability coverage %
        try:
            total_caps = db.session.query(func.count(BusinessCapability.id)).scalar() or 0
            if total_caps > 0:
                from sqlalchemy import text
                mapped = db.session.execute(text(  # tenant-filtered: scoped via parent FK (aggregate)
                    "SELECT COUNT(DISTINCT business_capability_id) FROM application_capability_mapping"
                )).scalar() or 0
                coverage_pct = (mapped / total_caps) * 100
            else:
                coverage_pct = 50  # No caps = neutral
        except Exception:
            coverage_pct = 50

        # Secondary signal: alert severity (max 30-point penalty)
        critical_count = sum(1 for a in alerts if a.get("priority") == "critical")
        high_count = sum(1 for a in alerts if a.get("priority") == "high")
        alert_penalty = min(critical_count * 8 + high_count * 4, 30)

        health = max(0, min(100, round(coverage_pct - alert_penalty)))
        return health

    def _generate_summary(self, alerts: List[Dict], recommendations: List[Dict]) -> Dict:
        """Generate summary statistics."""
        priority_counts = defaultdict(int)
        category_counts = defaultdict(int)
        type_counts = defaultdict(int)

        for alert in alerts:
            priority_counts[alert.get("priority", "low")] += 1
            category_counts[alert.get("category", "other")] += 1
            type_counts[alert.get("type", "other")] += 1

        return {
            "total_alerts": len(alerts),
            "total_recommendations": len(recommendations),
            "by_priority": dict(priority_counts),
            "by_category": dict(category_counts),
            "by_type": dict(type_counts),
            "critical_count": priority_counts.get("critical", 0),
            "high_count": priority_counts.get("high", 0),
        }

    def _filter_by_persona(self, data: Dict, persona: str) -> Dict:
        """Filter recommendations by persona focus areas."""
        if not persona or persona not in self.PERSONA_FOCUS:
            return data

        focus_areas = self.PERSONA_FOCUS[persona]

        # Filter alerts
        filtered_alerts = [
            alert
            for alert in data.get("alerts", [])
            if alert.get("category") in focus_areas or alert.get("impact") in focus_areas
        ]

        # Filter recommendations
        filtered_recs = [
            rec for rec in data.get("recommendations", []) if rec.get("category") in focus_areas
        ]

        return {
            **data,
            "alerts": filtered_alerts if filtered_alerts else data.get("alerts", [])[:10],
            "recommendations": filtered_recs
            if filtered_recs
            else data.get("recommendations", [])[:5],
            "persona_filter": persona,
        }

    def get_quick_stats(self) -> Dict[str, Any]:
        """Get quick statistics for dashboard display."""
        try:
            app_count = ApplicationComponent.query.count()
            capability_count = BusinessCapability.query.count()
            vendor_count = VendorOrganization.query.count()

            # Quick risk counts (simplified)
            apps_no_owner = (
                ApplicationComponent.query.filter(
                    or_(
                        ApplicationComponent.business_owner == None,
                        ApplicationComponent.business_owner == "",
                    )
                ).count()
                if hasattr(ApplicationComponent, "business_owner")
                else 0
            )

            return {
                "applications": app_count,
                "capabilities": capability_count,
                "vendors": vendor_count,
                "apps_needing_attention": apps_no_owner,
                "health_indicator": "good"
                if apps_no_owner < 5
                else "warning"
                if apps_no_owner < 15
                else "critical",
            }

        except Exception as e:
            logger.error(f"Error getting quick stats: {e}")
            return {"error": str(e)}
