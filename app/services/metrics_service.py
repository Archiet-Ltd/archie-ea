"""
Enterprise Metrics Service
Calculates cross-layer metrics for EA-CMDB-Financial federation
"""

import logging

from sqlalchemy import func

from app import db
from app.models.application_layer import ApplicationInterface
from app.models.application_portfolio import ApplicationComponent, VendorContract
from app.models.business_capabilities import BusinessCapability
from app.models.compliance_models import ComplianceRequirement
from app.models.enterprise_intelligence import ApplicationCost, ApplicationROI
from app.models.missing_capability_models import ApplicationCapability
from app.models.vendor import VendorOrganization, VendorProduct
from app.services.decorators import transactional

logger = logging.getLogger(__name__)


class MetricsService:
    """Enterprise metrics calculation service"""

    # ============================================================================
    # 1. PORTFOLIO HEALTH & STRATEGY METRICS
    # ============================================================================

    @staticmethod
    @transactional
    def get_application_lifecycle_distribution():
        """
        Application Count by Lifecycle Status
        Returns: {status: count, percentage}
        """
        try:
            query = db.session.query(
                ApplicationComponent.deployment_status,
                func.count(ApplicationComponent.id).label("count"),
            ).group_by(ApplicationComponent.deployment_status)

            results = query.all()
            total = sum(r.count for r in results)

            return [
                {
                    "status": r.deployment_status or "unknown",
                    "count": r.count,
                    "percentage": round((r.count / total * 100) if total > 0 else 0, 1),
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Error in get_application_lifecycle_distribution: {e}")
            return []

    @staticmethod
    @transactional
    def get_technical_debt_index():
        """
        Technical Debt Index across portfolio
        Returns: composite score 0 - 100 (lower is better)
        """
        try:
            # Get applications with technical debt data
            query = db.session.query(
                ApplicationComponent.id,
                ApplicationComponent.name,
                ApplicationComponent.business_criticality,
                ApplicationComponent.technical_debt_hours,
                ApplicationComponent.code_quality_score,
                ApplicationComponent.last_code_quality_scan,
            ).filter(
                ApplicationComponent.deployment_status.in_(["production", "staging"])
            )

            results = query.all()

            if not results:
                return {
                    "overall_index": 0,
                    "total_apps": 0,
                    "high_debt_apps": 0,
                    "avg_debt_hours": 0,
                    "total_debt_hours": 0,
                }

            total_debt_hours = 0
            apps_with_debt = 0
            high_debt_count = 0

            for r in results:
                if r.technical_debt_hours:
                    total_debt_hours += r.technical_debt_hours
                    apps_with_debt += 1
                    if r.technical_debt_hours > 100:  # High debt threshold
                        high_debt_count += 1

            avg_debt = total_debt_hours / apps_with_debt if apps_with_debt > 0 else 0

            # Calculate index (normalize to 0 - 100 scale)
            # Higher debt = higher index (worse)
            max_acceptable_debt = 50  # 50 hours per app is acceptable
            debt_index = min(100, (avg_debt / max_acceptable_debt) * 100)

            return {
                "overall_index": round(debt_index, 1),
                "total_apps": len(results),
                "high_debt_apps": high_debt_count,
                "avg_debt_hours": round(avg_debt, 1),
                "total_debt_hours": total_debt_hours,
            }
        except Exception as e:
            logger.error(f"Error in get_technical_debt_index: {e}")
            raise  # honest failure: never return a fabricated zero metric

    @staticmethod
    @transactional
    def get_capability_coverage_metrics():
        """
        Business Capability Coverage Analysis
        Returns: coverage statistics by capability level
        """
        try:
            # Get capability coverage by level
            query = (
                db.session.query(
                    BusinessCapability.level,
                    func.count(BusinessCapability.id).label("total_capabilities"),
                    func.count(ApplicationCapability.id).label("covered_capabilities"),
                )
                .outerjoin(
                    ApplicationCapability,
                    BusinessCapability.id
                    == ApplicationCapability.business_capability_id,
                )
                .group_by(BusinessCapability.level)
            )

            results = query.all()

            coverage_metrics = []
            for r in results:
                coverage_rate = (
                    (r.covered_capabilities / r.total_capabilities * 100)
                    if r.total_capabilities > 0
                    else 0
                )
                coverage_metrics.append(
                    {
                        "level": r.level or "unknown",
                        "total_capabilities": r.total_capabilities,
                        "covered_capabilities": r.covered_capabilities,
                        "coverage_rate": round(coverage_rate, 1),
                        "uncovered_capabilities": r.total_capabilities
                        - r.covered_capabilities,
                    }
                )

            # Calculate overall coverage
            total_caps = sum(r["total_capabilities"] for r in coverage_metrics)
            total_covered = sum(r["covered_capabilities"] for r in coverage_metrics)
            overall_coverage = (
                (total_covered / total_caps * 100) if total_caps > 0 else 0
            )

            return {
                "by_level": coverage_metrics,
                "overall_coverage": round(overall_coverage, 1),
                "total_capabilities": total_caps,
                "covered_capabilities": total_covered,
            }
        except Exception as e:
            logger.error(f"Error in get_capability_coverage_metrics: {e}")
            raise  # honest failure: never return a fabricated zero metric

    @staticmethod
    @transactional
    def get_capability_coverage():
        """
        Business Capability Coverage for Executive Dashboard
        Returns: detailed capability list with app counts and status
        """
        try:
            from app.models.application_capability import ApplicationCapabilityMapping

            # Get all capabilities with their app counts
            capabilities = db.session.query(BusinessCapability).all()

            cap_list = []
            total = len(capabilities)
            gaps = 0
            at_risk = 0
            covered = 0

            for cap in capabilities:
                # Count apps mapped to this capability
                app_count = (
                    # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                    db.session.query(ApplicationCapabilityMapping)
                    .filter_by(business_capability_id=cap.id)
                    .count()
                )

                # Determine status
                if app_count == 0:
                    status = "GAP - No Applications"
                    gaps += 1
                elif app_count == 1:
                    status = "AT RISK - Single App"
                    at_risk += 1
                else:
                    status = "Covered"
                    covered += 1

                cap_list.append(
                    {
                        "id": cap.id,
                        "name": cap.name or "Unnamed Capability",
                        "domain": cap.domain or "Unknown",
                        "level": cap.level or 1,
                        "app_count": app_count,
                        "maturity": cap.maturity_level or 1,
                        "status": status,
                    }
                )

            return {
                "capabilities": cap_list,
                "summary": {
                    "total": total,
                    "gaps": gaps,
                    "at_risk": at_risk,
                    "covered": covered,
                },
            }
        except Exception as e:
            logger.error(f"Error in get_capability_coverage: {e}")
            return {
                "capabilities": [],
                "summary": {"total": 0, "gaps": 0, "at_risk": 0, "covered": 0},
            }

    @staticmethod
    @transactional
    def get_strategic_alignment_score():
        """
        Strategic Alignment Score (Applications supporting strategic goals)
        Returns: alignment metrics 0 - 100
        """
        try:
            # Get strategic goals and their supporting applications
            # This is a simplified version - in production would use actual goal-app mappings
            strategic_apps = (
                db.session.query(ApplicationComponent)
                .filter(
                    ApplicationComponent.strategic_importance.in_(["high", "critical"])
                )
                .count()
            )

            total_apps = (
                db.session.query(ApplicationComponent)
                .filter(ApplicationComponent.deployment_status == "production")
                .count()
            )

            alignment_score = (
                (strategic_apps / total_apps * 100) if total_apps > 0 else 0
            )

            return {
                "alignment_score": round(alignment_score, 1),
                "strategic_apps": strategic_apps,
                "total_production_apps": total_apps,
                "non_strategic_apps": total_apps - strategic_apps,
            }
        except Exception as e:
            logger.error(f"Error in get_strategic_alignment_score: {e}")
            raise  # honest failure: never return a fabricated zero metric

    # ============================================================================
    # 2. VENDOR MANAGEMENT METRICS
    # ============================================================================

    @staticmethod
    @transactional
    def get_vendor_portfolio_metrics():
        """
        Vendor Portfolio Analysis
        Returns: vendor diversity, contract value, risk metrics
        """
        try:
            # Vendor diversity
            vendor_count = db.session.query(VendorOrganization).count()
            product_count = db.session.query(VendorProduct).count()

            # Contract analysis
            contracts = db.session.query(
                VendorContract.start_date,
                VendorContract.end_date,
                VendorContract.auto_renewal,
            ).all()

            # the money is read from the one contract ledger: active, in date, in the organisation's currency
            from app.services import vendor_register

            counted = vendor_register.contract_ledger()["counted"]
            total_contract_value, valued = vendor_register.stated_contract_value(counted)
            active_contracts = len(counted)

            # Risk metrics
            high_value_vendors = (
                db.session.query(VendorOrganization)
                .filter(VendorOrganization.annual_revenue > 1000000)
                .count()
            )

            single_source_risk = (
                db.session.query(BusinessCapability.id)
                .filter(BusinessCapability.primary_vendor_id.isnot(None))
                .count()
            )

            total_capabilities = db.session.query(BusinessCapability).count()
            single_source_percentage = (
                (single_source_risk / total_capabilities * 100)
                if total_capabilities > 0
                else 0
            )

            return {
                "vendor_diversity": {
                    "total_vendors": vendor_count,
                    "total_products": product_count,
                    "avg_products_per_vendor": round(product_count / vendor_count, 1)
                    if vendor_count > 0
                    else 0,
                },
                "contract_analysis": {
                    "total_contract_value": total_contract_value,
                    "active_contracts": active_contracts,
                    "avg_contract_value": round(total_contract_value / valued, 2) if valued else None,
                    "auto_renewal_rate": round(
                        len([c for c in contracts if c.auto_renewal])
                        / len(contracts)
                        * 100,
                        1,
                    )
                    if contracts
                    else 0,
                },
                "risk_metrics": {
                    "high_value_vendor_concentration": round(
                        high_value_vendors / vendor_count * 100, 1
                    )
                    if vendor_count > 0
                    else 0,
                    "single_source_dependency": round(single_source_percentage, 1),
                    "vendor_concentration_risk": "HIGH"
                    if single_source_percentage > 30
                    else "MEDIUM"
                    if single_source_percentage > 15
                    else "LOW",
                },
            }
        except Exception as e:
            logger.error(f"Error in get_vendor_portfolio_metrics: {e}")
            raise  # honest failure: never return a fabricated zero metric

    # ============================================================================
    # 3. COST & ROI METRICS
    # ============================================================================

    @staticmethod
    @transactional
    def get_it_cost_distribution():
        """
        IT Cost Distribution by Category
        Returns: cost breakdown by application category
        """
        try:
            # Get application costs by category
            query = (
                db.session.query(
                    ApplicationComponent.category,
                    func.sum(ApplicationCost.annual_cost).label("total_cost"),
                    func.count(ApplicationComponent.id).label("app_count"),
                )
                .outerjoin(
                    ApplicationCost,
                    ApplicationComponent.id == ApplicationCost.application_id,
                )
                .group_by(ApplicationComponent.category)
            )

            results = query.all()

            total_cost = sum(r.total_cost or 0 for r in results)

            cost_distribution = []
            for r in results:
                cost_percentage = (
                    (r.total_cost / total_cost * 100) if total_cost > 0 else 0
                )
                cost_distribution.append(
                    {
                        "category": r.category or "uncategorized",
                        "total_cost": r.total_cost or 0,
                        "app_count": r.app_count,
                        "cost_percentage": round(cost_percentage, 1),
                        "avg_cost_per_app": round((r.total_cost or 0) / r.app_count, 2)
                        if r.app_count > 0
                        else 0,
                    }
                )

            # Sort by cost (descending)
            cost_distribution.sort(key=lambda x: x["total_cost"], reverse=True)

            return {
                "by_category": cost_distribution,
                "total_it_cost": total_cost,
                "category_count": len(cost_distribution),
            }
        except Exception as e:
            logger.error(f"Error in get_it_cost_distribution: {e}")
            raise  # honest failure: never return a fabricated zero metric

    @staticmethod
    @transactional
    def get_roi_metrics():
        """
        Application ROI Analysis
        Returns: ROI metrics by application category
        """
        try:
            # Get ROI data
            query = (
                db.session.query(
                    ApplicationComponent.category,
                    func.avg(ApplicationCost.annual_cost).label("avg_cost"),
                    func.avg(ApplicationROI.roi_percentage).label("avg_roi"),
                    func.count(ApplicationComponent.id).label("app_count"),
                )
                .outerjoin(
                    ApplicationCost,
                    ApplicationComponent.id == ApplicationCost.application_id,
                )
                .outerjoin(
                    ApplicationROI,
                    ApplicationComponent.id == ApplicationROI.application_id,
                )
                .group_by(ApplicationComponent.category)
            )

            results = query.all()

            roi_metrics = []
            for r in results:
                roi_metrics.append(
                    {
                        "category": r.category or "uncategorized",
                        "avg_annual_cost": round(r.avg_cost or 0, 2),
                        "avg_roi_percentage": round(r.avg_roi or 0, 1),
                        "app_count": r.app_count,
                        "total_annual_cost": round((r.avg_cost or 0) * r.app_count, 2),
                        "total_roi": round((r.avg_roi or 0) * r.app_count, 1),
                    }
                )

            # Sort by ROI (descending)
            roi_metrics.sort(key=lambda x: x["avg_roi_percentage"], reverse=True)

            # Calculate portfolio averages
            portfolio_avg_cost = (
                sum(r["avg_annual_cost"] for r in roi_metrics) / len(roi_metrics)
                if roi_metrics
                else 0
            )
            portfolio_avg_roi = (
                sum(r["avg_roi_percentage"] for r in roi_metrics) / len(roi_metrics)
                if roi_metrics
                else 0
            )

            return {
                "by_category": roi_metrics,
                "portfolio_avg_cost": round(portfolio_avg_cost, 2),
                "portfolio_avg_roi": round(portfolio_avg_roi, 1),
                "total_categories": len(roi_metrics),
            }
        except Exception as e:
            logger.error(f"Error in get_roi_metrics: {e}")
            raise  # honest failure: never return a fabricated zero metric

    # ============================================================================
    # 4. COMPLIANCE & RISK METRICS
    # ============================================================================

    @staticmethod
    @transactional
    def get_compliance_status():
        """
        Compliance Status Overview
        Returns: compliance metrics by requirement type
        """
        try:
            # Get compliance requirements status
            query = db.session.query(
                ComplianceRequirement.requirement_type,
                ComplianceRequirement.compliance_status,
                func.count(ComplianceRequirement.id).label("count"),
            ).group_by(
                ComplianceRequirement.requirement_type,
                ComplianceRequirement.compliance_status,
            )

            results = query.all()

            # Organize by requirement type
            compliance_by_type = {}
            for r in results:
                req_type = r.requirement_type or "unknown"
                if req_type not in compliance_by_type:
                    compliance_by_type[req_type] = {
                        "compliant": 0,
                        "non_compliant": 0,
                        "in_progress": 0,
                        "total": 0,
                    }

                compliance_by_type[req_type][r.compliance_status or "unknown"] = r.count
                compliance_by_type[req_type]["total"] += r.count

            # Calculate compliance rates
            compliance_summary = []
            for req_type, data in compliance_by_type.items():
                compliance_rate = (
                    (data["compliant"] / data["total"] * 100)
                    if data["total"] > 0
                    else 0
                )
                compliance_summary.append(
                    {
                        "requirement_type": req_type,
                        "compliant": data["compliant"],
                        "non_compliant": data["non_compliant"],
                        "in_progress": data["in_progress"],
                        "total": data["total"],
                        "compliance_rate": round(compliance_rate, 1),
                    }
                )

            # Overall compliance
            total_requirements = sum(
                data["total"] for data in compliance_by_type.values()
            )
            total_compliant = sum(
                data["compliant"] for data in compliance_by_type.values()
            )
            overall_compliance_rate = (
                (total_compliant / total_requirements * 100)
                if total_requirements > 0
                else 0
            )

            return {
                "by_type": compliance_summary,
                "overall_compliance_rate": round(overall_compliance_rate, 1),
                "total_requirements": total_requirements,
                "risk_level": "HIGH"
                if overall_compliance_rate < 70
                else "MEDIUM"
                if overall_compliance_rate < 90
                else "LOW",
            }
        except Exception as e:
            logger.error(f"Error in get_compliance_status: {e}")
            raise  # honest failure: never return a fabricated zero metric

    # ============================================================================
    # 5. PERFORMANCE & OPERATIONAL METRICS
    # ============================================================================

    @staticmethod
    @transactional
    def get_system_performance_metrics():
        """
        System Performance Metrics
        Returns: uptime, response times, availability
        """
        try:
            # Get interface performance data
            query = (
                db.session.query(
                    ApplicationInterface.interface_type,
                    func.avg(ApplicationInterface.avg_response_time).label(
                        "avg_response_time"
                    ),
                    func.count(ApplicationInterface.id).label("interface_count"),
                    func.sum(ApplicationInterface.monthly_call_volume).label(
                        "total_calls"
                    ),
                )
                .filter(ApplicationInterface.status == "active")
                .group_by(ApplicationInterface.interface_type)
            )

            results = query.all()

            performance_metrics = []
            for r in results:
                performance_metrics.append(
                    {
                        "interface_type": r.interface_type or "unknown",
                        "avg_response_time": round(r.avg_response_time or 0, 2),
                        "interface_count": r.interface_count,
                        "total_calls": r.total_calls or 0,
                        "calls_per_interface": round(
                            (r.total_calls or 0) / r.interface_count, 2
                        )
                        if r.interface_count > 0
                        else 0,
                    }
                )

            # Calculate portfolio averages
            portfolio_avg_response = (
                sum(r["avg_response_time"] for r in performance_metrics)
                / len(performance_metrics)
                if performance_metrics
                else 0
            )
            total_interfaces = sum(r["interface_count"] for r in performance_metrics)
            total_calls = sum(r["total_calls"] for r in performance_metrics)

            # NB: the local is `portfolio_avg_response` (computed above); the response
            # KEY is "portfolio_avg_response_time". These three sites referenced the key
            # name as a variable, so this function raised NameError on every call, was
            # swallowed by the except below, and always returned the empty fallback —
            # meaning these metrics were never actually reported.
            return {
                "by_type": performance_metrics,
                "portfolio_avg_response_time": round(portfolio_avg_response, 2),
                "total_interfaces": total_interfaces,
                "total_monthly_calls": total_calls,
                "performance_grade": "EXCELLENT"
                if portfolio_avg_response < 100
                else "GOOD"
                if portfolio_avg_response < 500
                else "POOR",
            }
        except Exception as e:
            logger.error(f"Error in get_system_performance_metrics: {e}")
            raise  # honest failure: never return a fabricated zero metric

    # ============================================================================
    # 6. EXECUTIVE DASHBOARD METRICS
    # ============================================================================

    @staticmethod
    def get_executive_dashboard_metrics():
        """
        Consolidated Executive Dashboard Metrics
        Returns: KPI metrics for executive reporting
        """
        try:
            return {
                "portfolio_health": {
                    "lifecycle_distribution": MetricsService.get_application_lifecycle_distribution(),
                    "technical_debt_index": MetricsService.get_technical_debt_index(),
                    "capability_coverage": MetricsService.get_capability_coverage_metrics(),
                    "strategic_alignment": MetricsService.get_strategic_alignment_score(),
                },
                "vendor_management": {
                    "portfolio_metrics": MetricsService.get_vendor_portfolio_metrics()
                },
                "financial_performance": {
                    "cost_distribution": MetricsService.get_it_cost_distribution(),
                    "roi_analysis": MetricsService.get_roi_metrics(),
                },
                "risk_compliance": {
                    "compliance_status": MetricsService.get_compliance_status(),
                    "system_performance": MetricsService.get_system_performance_metrics(),
                },
            }
        except Exception as e:
            logger.error(f"Error in get_executive_dashboard_metrics: {e}")
            return {}

    @staticmethod
    def get_metrics_data():
        """Legacy method for backward compatibility"""
        return MetricsService.get_executive_dashboard_metrics()
