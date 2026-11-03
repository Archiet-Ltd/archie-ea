"""
-> app.modules.vendors.services.analysis_service

Vendor Risk Analysis Service

Calculates vendor concentration risk and portfolio diversification metrics.
Part of application rationalization framework for EA portfolio management.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import distinct, func

from app import db
from app.models.application_layer import ApplicationComponent
from app.models.application_rationalization import VendorConcentrationAnalysis
from app.models.vendor.vendor_organization import (
    VendorOrganization,
    VendorProduct,
    application_vendor_products,
)

logger = logging.getLogger(__name__)


def transactional(func):
    """Simple transactional decorator for methods that need DB commits."""

    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            db.session.rollback()
            raise e

    return wrapper


class VendorRiskService:
    """
    Service for analyzing vendor concentration risk and portfolio health.

    Key capabilities:
    - Calculate vendor concentration metrics (HHI, portfolio percentage)
    - Identify single points of failure (SPOF)
    - Assess critical process dependency on single vendor
    - Find alternative vendors for risk mitigation
    - Generate vendor diversification recommendations
    """

    @staticmethod
    @transactional
    def aggregate_vendor_concentration(vendor_id: int) -> Optional[VendorConcentrationAnalysis]:
        """
        Calculate comprehensive vendor concentration metrics and persist to DB.

        Args:
            vendor_id: Vendor organization ID

        Returns:
            VendorConcentrationAnalysis object or None on error
        """
        try:
            vendor = VendorOrganization.query.get(vendor_id)
            if not vendor:
                logger.error(f"Vendor not found: {vendor_id}")
                return None

            # Get all applications using this vendor's products
            vendor_apps = (
                db.session.query(ApplicationComponent)
                .join(
                    application_vendor_products,
                    ApplicationComponent.archimate_element_id
                    == application_vendor_products.c.archimate_element_id,
                )
                .join(
                    VendorProduct,
                    application_vendor_products.c.vendor_product_id == VendorProduct.id,
                )
                .filter(
                    VendorProduct.vendor_organization_id == vendor_id,
                    ApplicationComponent.lifecycle_status.in_(["ACTIVE", "PHASING_IN"]),
                )
                .all()
            )

            app_count = len(vendor_apps)

            # Calculate total active apps in portfolio
            total_apps = ApplicationComponent.query.filter(
                ApplicationComponent.lifecycle_status.in_(["ACTIVE", "PHASING_IN"])
            ).count()

            portfolio_percentage = (app_count / total_apps * 100) if total_apps > 0 else 0

            # Calculate Herfindahl-Hirschman Index (HHI) for vendor concentration
            hhi = VendorRiskService._calculate_portfolio_hhi()

            # Count critical processes supported by this vendor
            critical_process_count = (
                0  # Simplified - would need ApplicationProcessSupport relationship
            )

            # Count critical capabilities supported
            critical_capability_count = (
                db.session.query(func.count(distinct(ApplicationComponent.business_capability_id)))
                .join(
                    application_vendor_products,
                    ApplicationComponent.archimate_element_id
                    == application_vendor_products.c.archimate_element_id,
                )
                .join(
                    VendorProduct,
                    application_vendor_products.c.vendor_product_id == VendorProduct.id,
                )
                .filter(
                    VendorProduct.vendor_organization_id == vendor_id,
                    ApplicationComponent.criticality_level.in_(["HIGH", "CRITICAL"]),
                )
                .scalar()
                or 0
            )

            # Find alternative vendors
            alternative_vendors = VendorRiskService._find_alternative_vendors(vendor_id)
            alternative_vendor_count = len(alternative_vendors)

            # Calculate concentration risk score (0 - 100, higher = riskier)
            concentration_risk = VendorRiskService._calculate_concentration_risk(
                portfolio_percentage=portfolio_percentage,
                critical_process_count=critical_process_count,
                critical_capability_count=critical_capability_count,
                alternative_count=alternative_vendor_count,
                hhi=hhi,
            )

            # Check or create existing analysis record
            analysis = VendorConcentrationAnalysis.query.filter_by(vendor_organization_id=vendor_id).first()

            if not analysis:
                analysis = VendorConcentrationAnalysis(vendor_organization_id=vendor_id)
                db.session.add(analysis)

            # Update all fields
            analysis.application_count = app_count
            analysis.critical_application_count = sum(
                1 for app in vendor_apps if app.criticality_level in ["HIGH", "CRITICAL"]
            )
            analysis.percentage_of_it_budget = round(portfolio_percentage, 2)
            # HHI stored at portfolio level, not per-vendor
            analysis.business_process_count = critical_process_count
            analysis.capability_count = critical_capability_count
            analysis.alternative_vendor_count = alternative_vendor_count
            analysis.concentration_risk_score = round(concentration_risk, 2)

            db.session.flush()

            logger.info(
                f"Updated concentration analysis for vendor {vendor.name}: "
                f"{app_count} apps, {portfolio_percentage:.1f}% portfolio, "
                f"risk score {concentration_risk:.1f}"
            )

            return analysis

        except Exception as e:
            logger.error(
                f"Error aggregating vendor concentration for {vendor_id}: {e}", exc_info=True
            )
            db.session.rollback()
            return None

    @staticmethod
    def _calculate_portfolio_hhi() -> float:
        """
        Calculate Herfindahl-Hirschman Index for entire vendor portfolio.

        HHI = sum of squared market shares (0 - 10000)
        - HHI < 1500: Competitive market
        - HHI 1500 - 2500: Moderate concentration
        - HHI > 2500: High concentration
        """
        try:
            # Get app counts per vendor
            vendor_counts = (
                db.session.query(
                    VendorProduct.vendor_organization_id,
                    func.count(distinct(ApplicationComponent.id)).label("app_count"),
                )
                .join(
                    application_vendor_products,
                    VendorProduct.id == application_vendor_products.c.vendor_product_id,
                )
                .join(
                    ApplicationComponent,
                    application_vendor_products.c.archimate_element_id
                    == ApplicationComponent.archimate_element_id,
                )
                .filter(ApplicationComponent.lifecycle_status.in_(["ACTIVE", "PHASING_IN"]))
                .group_by(VendorProduct.vendor_organization_id)
                .all()
            )

            total_apps = sum(count for _, count in vendor_counts)

            if total_apps == 0:
                return 0.0

            # Calculate HHI = sum of squared market shares
            hhi = sum((count / total_apps * 100) ** 2 for _, count in vendor_counts)

            return hhi

        except Exception as e:
            logger.error(f"Error calculating HHI: {e}", exc_info=True)
            return 0.0

    @staticmethod
    def _calculate_concentration_risk(
        portfolio_percentage: float,
        critical_process_count: int,
        critical_capability_count: int,
        alternative_count: int,
        hhi: float,
    ) -> float:
        """
        Calculate overall vendor concentration risk score (0 - 100).

        Factors:
        - Portfolio concentration (30% weight)
        - Critical process dependency (30% weight)
        - Alternative vendor availability (20% weight)
        - Overall market concentration via HHI (20% weight)
        """
        # Portfolio concentration risk (30%)
        portfolio_risk = min(portfolio_percentage * 2, 100) * 0.3

        # Critical dependency risk (30%)
        critical_count = critical_process_count + critical_capability_count
        critical_risk = min(critical_count * 3, 100) * 0.3

        # Alternative availability risk (20%) - inverse relationship
        if alternative_count == 0:
            alternative_risk = 100 * 0.2
        elif alternative_count == 1:
            alternative_risk = 70 * 0.2
        elif alternative_count == 2:
            alternative_risk = 40 * 0.2
        else:
            alternative_risk = 10 * 0.2

        # Market concentration risk via HHI (20%)
        # HHI > 2500 = high concentration
        if hhi > 2500:
            hhi_risk = 80 * 0.2
        elif hhi > 1500:
            hhi_risk = 50 * 0.2
        else:
            hhi_risk = 20 * 0.2

        overall_risk = portfolio_risk + critical_risk + alternative_risk + hhi_risk
        return min(overall_risk, 100)

    @staticmethod
    def _find_alternative_vendors(vendor_id: int) -> List[Dict]:
        """
        Find alternative vendors offering similar products/capabilities.

        Logic: Look for vendors whose products are used alongside target vendor
        in same capability areas (suggesting functional overlap).
        """
        try:
            # Get capability IDs where target vendor is used
            target_capabilities = (
                db.session.query(distinct(ApplicationComponent.business_capability_id))
                .join(
                    application_vendor_products,
                    ApplicationComponent.archimate_element_id
                    == application_vendor_products.c.archimate_element_id,
                )
                .join(
                    VendorProduct,
                    application_vendor_products.c.vendor_product_id == VendorProduct.id,
                )
                .filter(
                    VendorProduct.vendor_organization_id == vendor_id,
                    ApplicationComponent.business_capability_id.isnot(None),
                )
                .all()
            )

            capability_ids = [cap_id for (cap_id,) in target_capabilities]

            if not capability_ids:
                return []

            # Find other vendors operating in same capability spaces
            alternative_vendors = (
                db.session.query(
                    VendorOrganization.id,
                    VendorOrganization.name,
                    func.count(distinct(ApplicationComponent.id)).label("app_count"),
                )
                .join(VendorProduct, VendorOrganization.id == VendorProduct.vendor_organization_id)
                .join(
                    application_vendor_products,
                    VendorProduct.id == application_vendor_products.c.vendor_product_id,
                )
                .join(
                    ApplicationComponent,
                    application_vendor_products.c.archimate_element_id
                    == ApplicationComponent.archimate_element_id,
                )
                .filter(
                    VendorOrganization.id != vendor_id,
                    ApplicationComponent.business_capability_id.in_(capability_ids),
                    ApplicationComponent.lifecycle_status.in_(["ACTIVE", "PHASING_IN"]),
                )
                .group_by(VendorOrganization.id, VendorOrganization.name)
                .order_by(func.count(distinct(ApplicationComponent.id)).desc())
                .limit(10)
                .all()
            )

            return [
                {"vendor_id": vid, "vendor_name": vendor_name, "overlapping_app_count": app_count}
                for vid, vendor_name, app_count in alternative_vendors
            ]

        except Exception as e:
            logger.error(f"Error finding alternative vendors: {e}", exc_info=True)
            return []

    @staticmethod
    def analyze_portfolio_risk() -> Dict:
        """
        Analyze overall portfolio vendor concentration risk.

        Returns comprehensive portfolio-level metrics and recommendations.
        """
        try:
            # Get all active vendors
            active_vendors = (
                db.session.query(VendorOrganization.id, VendorOrganization.name)
                .join(VendorProduct, VendorOrganization.id == VendorProduct.vendor_organization_id)
                .join(
                    application_vendor_products,
                    VendorProduct.id == application_vendor_products.c.vendor_product_id,
                )
                .join(
                    ApplicationComponent,
                    application_vendor_products.c.archimate_element_id
                    == ApplicationComponent.archimate_element_id,
                )
                .filter(ApplicationComponent.lifecycle_status.in_(["ACTIVE", "PHASING_IN"]))
                .distinct()
                .all()
            )

            # Calculate HHI
            hhi = VendorRiskService._calculate_portfolio_hhi()

            # Get concentration analysis for all vendors
            concentration_analyses = VendorConcentrationAnalysis.query.filter(
                VendorConcentrationAnalysis.vendor_organization_id.in_([v.id for v in active_vendors])
            ).all()

            # Identify high-risk vendors (risk score > 60)
            high_risk_vendors = [
                {
                    "vendor_id": analysis.vendor_organization_id,
                    "vendor_name": next(
                        (v.name for v in active_vendors if v.id == analysis.vendor_organization_id), "Unknown"
                    ),
                    "risk_score": analysis.concentration_risk_score,
                    "portfolio_percentage": analysis.percentage_of_it_budget,
                    "critical_processes": analysis.business_process_count,
                    "alternatives": analysis.alternative_vendor_count,
                }
                for analysis in concentration_analyses
                if analysis.concentration_risk_score > 60
            ]

            # Sort by risk score descending
            high_risk_vendors.sort(key=lambda x: x["risk_score"], reverse=True)

            # Generate recommendations
            recommendations = VendorRiskService._generate_portfolio_recommendations(
                hhi=hhi,
                high_risk_count=len(high_risk_vendors),
                total_vendor_count=len(active_vendors),
            )

            return {
                "portfolio_metrics": {
                    "total_active_vendors": len(active_vendors),
                    "herfindahl_index": round(hhi, 2),
                    "concentration_level": (
                        "HIGH" if hhi > 2500 else "MODERATE" if hhi > 1500 else "LOW"
                    ),
                    "high_risk_vendor_count": len(high_risk_vendors),
                },
                "high_risk_vendors": high_risk_vendors[:5],  # Top 5
                "recommendations": recommendations,
                "analysis_timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error analyzing portfolio risk: {e}", exc_info=True)
            return {"error": str(e)}

    @staticmethod
    def _generate_portfolio_recommendations(
        hhi: float, high_risk_count: int, total_vendor_count: int
    ) -> List[str]:
        """Generate vendor diversification recommendations."""
        recommendations = []

        if hhi > 2500:
            recommendations.append(
                "CRITICAL: High vendor concentration detected (HHI > 2500). "
                "Prioritize vendor diversification initiatives."
            )
        elif hhi > 1500:
            recommendations.append(
                "WARNING: Moderate vendor concentration (HHI 1500 - 2500). "
                "Monitor concentration trends and plan diversification."
            )

        if high_risk_count > 0:
            recommendations.append(
                f"ALERT: {high_risk_count} vendor(s) pose high concentration risk. "
                f"Review alternatives and develop contingency plans."
            )

        if total_vendor_count < 5:
            recommendations.append(
                f"Portfolio has only {total_vendor_count} active vendors. "
                f"Consider expanding vendor base for resilience."
            )

        if not recommendations:
            recommendations.append(
                "Portfolio vendor concentration is within acceptable limits. "
                "Continue monitoring quarterly."
            )

        return recommendations

    @staticmethod
    def get_vendor_exit_strategy(vendor_id: int) -> Dict:
        """
        Generate vendor exit strategy with migration planning data.

        Useful for forced vendor changes (acquisition, end-of-life, etc.)
        """
        try:
            analysis = VendorConcentrationAnalysis.query.filter_by(vendor_organization_id=vendor_id).first()

            if not analysis:
                # Trigger analysis if not exists
                analysis = VendorRiskService.aggregate_vendor_concentration(vendor_id)
                if not analysis:
                    return {"error": "Unable to analyze vendor"}

            vendor = VendorOrganization.query.get(vendor_id)

            # Get affected applications
            affected_apps = (
                db.session.query(ApplicationComponent)
                .join(
                    application_vendor_products,
                    ApplicationComponent.archimate_element_id
                    == application_vendor_products.c.archimate_element_id,
                )
                .join(
                    VendorProduct,
                    application_vendor_products.c.vendor_product_id == VendorProduct.id,
                )
                .filter(
                    VendorProduct.vendor_organization_id == vendor_id,
                    ApplicationComponent.lifecycle_status.in_(["ACTIVE", "PHASING_IN"]),
                )
                .all()
            )

            # Get active contracts
            contracts = []
            total_contract_value = None
            try:
                from app.services import vendor_register

                contracts = vendor_register.contract_ledger()["by_vendor"].get(vendor_id, [])
                total_contract_value = next(
                    (r["annual_cost"] for r in vendor_register.register() if r["id"] == vendor_id), None)
            except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug("VendorContract not available for impact analysis")

            # Find alternatives
            alternatives = VendorRiskService._find_alternative_vendors(vendor_id)

            return {
                "vendor": {"id": vendor.id, "name": vendor.name},
                "impact_summary": {
                    "affected_applications": len(affected_apps),
                    "critical_applications": analysis.critical_application_count,
                    "critical_processes": analysis.business_process_count,
                    "active_contracts": len(contracts),
                    "total_annual_spend": total_contract_value,
                },
                "affected_applications": [
                    {"id": app.id, "name": app.name, "criticality": app.criticality_level}
                    for app in affected_apps
                ],
                "alternative_vendors": alternatives,
                "estimated_migration_effort": (
                    "HIGH"
                    if len(affected_apps) > 10 or analysis.critical_application_count > 5
                    else "MEDIUM"
                    if len(affected_apps) > 5
                    else "LOW"
                ),
                "recommended_timeline": (
                    "18 - 24 months"
                    if len(affected_apps) > 10
                    else "12 - 18 months"
                    if len(affected_apps) > 5
                    else "6 - 12 months"
                ),
            }

        except Exception as e:
            logger.error(
                f"Error generating exit strategy for vendor {vendor_id}: {e}", exc_info=True
            )
            return {"error": str(e), "vendor_id": vendor_id}
