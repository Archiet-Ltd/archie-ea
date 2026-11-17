"""
-> app.modules.vendors.services.analysis_service

Vendor Analysis Service

Comprehensive vendor and product analysis for Enterprise Architecture decision-making.
Supports comparison, recommendations, and portfolio analysis.
"""


# ApplicationComponent import removed - not used in vendor analysis
# from app.models.application_portfolio import ApplicationComponent
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import joinedload

from app.models.business_capabilities import BusinessCapability
from app.models.vendor.vendor_organization import (
    VendorOrganization,
    VendorProduct,
    VendorProductCapability,
)


class VendorAnalysisService:
    """Service for comprehensive vendor analysis and comparison."""

    def __init__(self):
        self.app = None

    def init_app(self, app):
        """Initialize with Flask app context."""
        self.app = app

    def analyze_vendors(
        self,
        vendor_ids: Optional[List[int]] = None,
        capability_ids: Optional[List[int]] = None,
        product_families: Optional[List[str]] = None,
        deployment_models: Optional[List[str]] = None,
        contract_statuses: Optional[List[str]] = None,
        min_readiness_score: Optional[int] = None,
        max_cost: Optional[Decimal] = None,
        min_cost: Optional[Decimal] = None,
        technology_stack: Optional[str] = None,
        consider_existing_apps: bool = True,
        consider_existing_vendors: bool = True,
    ) -> Dict:
        """
        Perform comprehensive vendor analysis based on filters.

        Returns:
            Dict with analysis results including:
            - summary: Key metrics
            - products: Product comparison data
            - capability_matrix: Capability coverage matrix
            - recommendations: AI-powered recommendations
            - vendor_portfolios: Portfolio-level analysis
        """
        with self.app.app_context():
            # Build base query
            query = VendorProduct.query.options(
                joinedload(VendorProduct.vendor_organization),
                joinedload(VendorProduct.capability_mappings).joinedload(
                    VendorProductCapability.business_capability
                ),
            )

            # Apply filters
            if vendor_ids:
                query = query.filter(VendorProduct.vendor_organization_id.in_(vendor_ids))

            if product_families:
                query = query.filter(VendorProduct.product_family_name.in_(product_families))

            if deployment_models:
                query = query.filter(VendorProduct.deployment_model.in_(deployment_models))

            if contract_statuses:
                query = query.join(VendorOrganization).filter(
                    VendorOrganization.contract_status.in_(contract_statuses)
                )

            if min_readiness_score is not None:
                query = query.join(VendorOrganization).filter(
                    VendorOrganization.enterprise_readiness_score >= min_readiness_score
                )

            if technology_stack:
                query = query.filter(
                    VendorProduct.primary_technology.ilike(f"%{technology_stack}%")
                )

            # Filter by capabilities if specified - do this at SQL level to avoid N+1
            if capability_ids:
                query = query.join(VendorProductCapability).filter(
                    VendorProductCapability.business_capability_id.in_(capability_ids)
                ).distinct()

            products = query.all()

            # Filter by cost if specified (must be done in Python due to Decimal comparisons)
            if min_cost is not None or max_cost is not None:
                products = self._filter_by_cost(products, min_cost, max_cost)

            # Build analysis results
            analysis = {
                "summary": self._calculate_summary(products, capability_ids),
                "products": self._build_product_comparison(products),
                "capability_matrix": self._build_capability_matrix(products, capability_ids),
                "recommendations": self._generate_recommendations(
                    products, capability_ids, consider_existing_apps, consider_existing_vendors
                ),
                "vendor_portfolios": self._analyze_vendor_portfolios(products),
                "technology_stacks": self._analyze_technology_stacks(products),
                "cost_analysis": self._analyze_costs(products),
            }

            return analysis

    def _filter_by_cost(
        self,
        products: List[VendorProduct],
        min_cost: Optional[Decimal],
        max_cost: Optional[Decimal],
    ) -> List[VendorProduct]:
        """Filter products by cost range."""
        filtered = []
        for product in products:
            cost = product.base_license_cost_annual or Decimal(0)
            if min_cost is not None and cost < min_cost:
                continue
            if max_cost is not None and cost > max_cost:
                continue
            filtered.append(product)
        return filtered

    def _calculate_summary(
        self, products: List[VendorProduct], capability_ids: Optional[List[int]]
    ) -> Dict:
        """Calculate summary statistics."""
        if not products:
            return {
                "total_products": 0,
                "total_vendors": 0,
                "total_capabilities_covered": 0,
                "avg_coverage": 0,
                "total_estimated_cost": 0,
            }

        vendor_ids = set(p.vendor_organization_id for p in products)
        all_capability_ids = set()
        total_cost = Decimal(0)
        coverage_scores = []

        for product in products:
            if product.base_license_cost_annual:
                total_cost += product.base_license_cost_annual

            for mapping in product.capability_mappings:
                all_capability_ids.add(mapping.business_capability_id)
                if mapping.coverage_percentage:
                    coverage_scores.append(mapping.coverage_percentage)

        avg_coverage = sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0

        # If specific capabilities requested, calculate coverage for those
        requested_capabilities_covered = 0
        if capability_ids:
            for cap_id in capability_ids:
                for product in products:
                    for mapping in product.capability_mappings:
                        if mapping.business_capability_id == cap_id:
                            requested_capabilities_covered += 1
                            break

        return {
            "total_products": len(products),
            "total_vendors": len(vendor_ids),
            "total_capabilities_covered": len(all_capability_ids),
            "requested_capabilities_covered": requested_capabilities_covered
            if capability_ids
            else None,
            "avg_coverage": round(avg_coverage, 1),
            "total_estimated_cost": float(total_cost),
            "avg_cost_per_product": float(total_cost / len(products)) if products else 0,
        }

    def _build_product_comparison(self, products: List[VendorProduct]) -> List[Dict]:
        """Build side-by-side product comparison data."""
        comparison = []

        for product in products:
            vendor = product.vendor_organization

            # Calculate overall fit score
            fit_score = self._calculate_product_fit_score(product)

            # Get capability coverage summary
            capability_count = (
                len(product.capability_mappings) if product.capability_mappings else 0
            )
            if capability_count > 0:
                coverage_values = [
                    m.coverage_percentage
                    for m in product.capability_mappings
                    if m.coverage_percentage is not None
                ]
                avg_coverage = sum(coverage_values) / len(coverage_values) if coverage_values else 0
            else:
                avg_coverage = 0

            comparison.append(
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    "vendor_id": vendor.id,
                    "vendor_name": vendor.name,
                    "product_family": getattr(product, "product_family_name", None),
                    "deployment_model": product.deployment_model,
                    "licensing_model": product.licensing_model,
                    "version": product.version,
                    "base_license_cost_annual": float(product.base_license_cost_annual or 0),
                    "implementation_cost_estimate": float(
                        product.implementation_cost_estimate or 0
                    ),
                    "enterprise_readiness_score": vendor.enterprise_readiness_score,
                    "strategic_tier": vendor.strategic_tier,
                    "partnership_level": vendor.partnership_level,
                    "capability_count": capability_count,
                    "avg_coverage_percentage": round(avg_coverage, 1),
                    "fit_score": fit_score,
                    "technology_stack": product.primary_technology,
                    "api_availability": product.api_availability,
                    "contract_status": vendor.contract_status or "catalog",
                }
            )

        # Sort by fit score descending
        comparison.sort(key=lambda x: x["fit_score"], reverse=True)

        return comparison

    def _calculate_product_fit_score(self, product: VendorProduct) -> float:
        """Calculate overall fit score for a product (0 - 100)."""
        scores = []
        weights = []

        vendor = product.vendor_organization

        # Enterprise readiness (30% weight)
        if vendor.enterprise_readiness_score:
            scores.append(vendor.enterprise_readiness_score * 0.3)
            weights.append(0.3)

        # Average capability coverage (40% weight)
        if product.capability_mappings:
            coverage_values = [
                m.coverage_percentage
                for m in product.capability_mappings
                if m.coverage_percentage is not None
            ]
            if coverage_values:
                avg_coverage = sum(coverage_values) / len(coverage_values)
            else:
                avg_coverage = 0
            scores.append(avg_coverage * 0.4)
            weights.append(0.4)

        # Maturity (20% weight)
        if product.capability_mappings:
            avg_maturity = sum(
                (m.maturity_level or 3) * 20 for m in product.capability_mappings
            ) / len(product.capability_mappings)
            scores.append(avg_maturity * 0.2)
            weights.append(0.2)

        # Cost efficiency (10% weight) - lower cost is better
        if product.base_license_cost_annual:
            # Normalize cost (assume max $1M, invert so lower cost = higher score)
            cost_score = max(0, 100 - (float(product.base_license_cost_annual) / 10000))
            scores.append(cost_score * 0.1)
            weights.append(0.1)

        if not scores:
            return 50.0  # Default score

        # Weighted average
        total_weight = sum(weights)
        if total_weight == 0:
            return 50.0

        return round(sum(scores) / total_weight, 1)

    def _build_capability_matrix(
        self, products: List[VendorProduct], capability_ids: Optional[List[int]]
    ) -> Dict:
        """Build capability coverage matrix (products vs capabilities)."""
        # Get all relevant capabilities
        if capability_ids:
            capabilities = BusinessCapability.query.filter(
                BusinessCapability.id.in_(capability_ids)
            ).all()
        else:
            # Get all capabilities covered by these products
            all_cap_ids = set()
            for product in products:
                for mapping in product.capability_mappings:
                    all_cap_ids.add(mapping.business_capability_id)

            capabilities = BusinessCapability.query.filter(
                BusinessCapability.id.in_(list(all_cap_ids))
            ).all()

        matrix = {"capabilities": [], "products": [], "coverage_data": {}}

        # Build capability list
        for cap in capabilities:
            matrix["capabilities"].append(
                {
                    "id": cap.id,
                    "name": cap.name,
                    "code": cap.code,
                    "level": cap.level,
                    "category": cap.category,
                }
            )

        # Build product list and coverage data
        for product in products:
            product_data = {
                "product_id": product.id,
                "product_name": product.name,
                "vendor_name": product.vendor_organization.name,
            }
            matrix["products"].append(product_data)

            # Get coverage for each capability
            for cap in capabilities:
                key = f"{product.id}_{cap.id}"
                mapping = next(
                    (m for m in product.capability_mappings if m.business_capability_id == cap.id),
                    None,
                )

                if mapping:
                    matrix["coverage_data"][key] = {
                        "coverage_percentage": mapping.coverage_percentage or 0,
                        "maturity_level": mapping.maturity_level or 0,
                        "fit_score": mapping.fit_score or 0,
                        "implementation_complexity": mapping.implementation_complexity or 0,
                        "customization_required": mapping.customization_required or False,
                    }
                else:
                    matrix["coverage_data"][key] = {
                        "coverage_percentage": 0,
                        "maturity_level": 0,
                        "fit_score": 0,
                        "implementation_complexity": 0,
                        "customization_required": False,
                    }

        return matrix

    def _generate_recommendations(
        self,
        products: List[VendorProduct],
        capability_ids: Optional[List[int]],
        consider_existing_apps: bool,
        consider_existing_vendors: bool,
    ) -> Dict:
        """Generate AI-powered recommendations."""
        recommendations = {
            "best_fit_products": [],
            "cost_optimization": [],
            "vendor_consolidation": [],
            "technology_alignment": [],
            "risk_assessment": [],
        }

        if not products:
            return recommendations

        # Best fit products
        product_scores = []
        for product in products:
            fit_score = self._calculate_product_fit_score(product)
            product_scores.append({"product": product, "fit_score": fit_score})

        product_scores.sort(key=lambda x: x["fit_score"], reverse=True)

        recommendations["best_fit_products"] = [
            {
                "product_id": item["product"].id,
                "product_name": item["product"].name,
                "vendor_name": item["product"].vendor_organization.name,
                "fit_score": item["fit_score"],
                "rationale": self._generate_fit_rationale(item["product"], item["fit_score"]),
            }
            for item in product_scores[:5]  # Top 5
        ]

        # Cost optimization
        recommendations["cost_optimization"] = self._analyze_cost_optimization(products)

        # Vendor consolidation
        if consider_existing_vendors:
            recommendations["vendor_consolidation"] = self._analyze_vendor_consolidation(products)

        # Technology alignment
        recommendations["technology_alignment"] = self._analyze_technology_alignment(products)

        # Risk assessment
        recommendations["risk_assessment"] = self._assess_risks(products)

        return recommendations

    def _generate_fit_rationale(self, product: VendorProduct, fit_score: float) -> str:
        """Generate rationale for product fit score."""
        vendor = product.vendor_organization
        reasons = []

        if vendor.enterprise_readiness_score and vendor.enterprise_readiness_score >= 80:
            reasons.append("High enterprise readiness")

        if product.capability_mappings:
            avg_coverage = sum(
                m.coverage_percentage for m in product.capability_mappings if m.coverage_percentage
            ) / len(product.capability_mappings)
            if avg_coverage >= 80:
                reasons.append("Strong capability coverage")

        if vendor.strategic_tier == "tier_1_strategic":
            reasons.append("Strategic vendor partnership")

        if product.api_availability:
            reasons.append("API integration available")

        if not reasons:
            reasons.append("Meets basic requirements")

        return "; ".join(reasons)

    def _analyze_cost_optimization(self, products: List[VendorProduct]) -> List[Dict]:
        """Analyze cost optimization opportunities."""
        opportunities = []

        # Group by product family
        families = {}
        for product in products:
            family = getattr(product, "product_family_name", None) or "Other"
            if family not in families:
                families[family] = []
            families[family].append(product)

        # Find families with multiple options
        for family, family_products in families.items():
            if len(family_products) > 1:
                # Sort by cost
                sorted_products = sorted(
                    family_products, key=lambda p: float(p.base_license_cost_annual or 0)
                )

                if len(sorted_products) >= 2:
                    cheapest = sorted_products[0]
                    most_expensive = sorted_products[-1]
                    savings = float(
                        (most_expensive.base_license_cost_annual or 0)
                        - (cheapest.base_license_cost_annual or 0)
                    )

                    if savings > 0:
                        opportunities.append(
                            {
                                "family": family,
                                "recommendation": f"Consider {cheapest.name} over {most_expensive.name}",
                                "potential_savings": savings,
                                "cheapest_product_id": cheapest.id,
                                "expensive_product_id": most_expensive.id,
                            }
                        )

        return sorted(opportunities, key=lambda x: x["potential_savings"], reverse=True)[:5]

    def _analyze_vendor_consolidation(self, products: List[VendorProduct]) -> List[Dict]:
        """Analyze vendor consolidation opportunities."""
        opportunities = []

        # Group by vendor
        vendors = {}
        for product in products:
            vendor_id = product.vendor_organization_id
            if vendor_id not in vendors:
                vendors[vendor_id] = {"vendor": product.vendor_organization, "products": []}
            vendors[vendor_id]["products"].append(product)

        # Find vendors with multiple products
        for vendor_id, vendor_data in vendors.items():
            if len(vendor_data["products"]) > 1:
                total_cost = sum(
                    float(p.base_license_cost_annual or 0) for p in vendor_data["products"]
                )

                opportunities.append(
                    {
                        "vendor_id": vendor_id,
                        "vendor_name": vendor_data["vendor"].name,
                        "product_count": len(vendor_data["products"]),
                        "total_cost": total_cost,
                        "recommendation": f"Consolidate {len(vendor_data['products'])} products from {vendor_data['vendor'].name}",
                        "potential_benefits": [
                            "Reduced vendor management overhead",
                            "Potential volume discounts",
                            "Simplified integration",
                        ],
                    }
                )

        return sorted(opportunities, key=lambda x: x["product_count"], reverse=True)[:5]

    def _analyze_technology_alignment(self, products: List[VendorProduct]) -> List[Dict]:
        """Analyze technology stack alignment."""
        alignment = []

        # Group by technology
        tech_stacks = {}
        for product in products:
            tech = product.primary_technology or "Unknown"
            if tech not in tech_stacks:
                tech_stacks[tech] = []
            tech_stacks[tech].append(product)

        for tech, tech_products in tech_stacks.items():
            if len(tech_products) > 1:
                alignment.append(
                    {
                        "technology": tech,
                        "product_count": len(tech_products),
                        "products": [p.name for p in tech_products],
                        "recommendation": f"{len(tech_products)} products use {tech} - good technology alignment",
                    }
                )

        return alignment[:5]

    def _assess_risks(self, products: List[VendorProduct]) -> List[Dict]:
        """Assess risks for products."""
        risks = []

        for product in products:
            vendor = product.vendor_organization
            risk_items = []
            risk_level = "low"

            # Low enterprise readiness
            if vendor.enterprise_readiness_score and vendor.enterprise_readiness_score < 60:
                risk_items.append("Low enterprise readiness score")
                risk_level = "medium"

            # No strategic partnership
            if vendor.partnership_level in [None, "none"]:
                risk_items.append("No strategic partnership")
                risk_level = "medium"

            # High customization required
            high_customization = any(
                m.customization_required and m.customization_effort in ["high", "very_high"]
                for m in product.capability_mappings
            )
            if high_customization:
                risk_items.append("High customization required")
                risk_level = "high"

            # Vendor lock-in risk
            if vendor.strategic_tier == "tier_1_strategic" and len(vendor.products) > 3:
                risk_items.append("Potential vendor lock-in")
                risk_level = "medium"

            if risk_items:
                risks.append(
                    {
                        "product_id": product.id,
                        "product_name": product.name,
                        "vendor_name": vendor.name,
                        "risk_level": risk_level,
                        "risks": risk_items,
                        "mitigation": self._suggest_mitigation(product, risk_items),
                    }
                )

        return sorted(
            risks, key=lambda x: {"high": 3, "medium": 2, "low": 1}[x["risk_level"]], reverse=True
        )

    def _suggest_mitigation(self, product: VendorProduct, risks: List[str]) -> List[str]:
        """Suggest risk mitigation strategies."""
        mitigations = []

        if "Low enterprise readiness score" in risks:
            mitigations.append("Request vendor roadmap and support commitments")

        if "No strategic partnership" in risks:
            mitigations.append("Consider establishing strategic partnership agreement")

        if "High customization required" in risks:
            mitigations.append("Evaluate alternative products with better out-of-box fit")

        if "Potential vendor lock-in" in risks:
            mitigations.append("Develop exit strategy and maintain alternative options")

        return mitigations

    def _analyze_vendor_portfolios(self, products: List[VendorProduct]) -> List[Dict]:
        """Analyze vendor portfolios."""
        portfolios = {}

        for product in products:
            vendor_id = product.vendor_organization_id
            if vendor_id not in portfolios:
                vendor = product.vendor_organization
                portfolios[vendor_id] = {
                    "vendor_id": vendor_id,
                    "vendor_name": vendor.name,
                    "products": [],
                    "total_products": 0,
                    "total_cost": Decimal(0),
                    "avg_readiness": vendor.enterprise_readiness_score,
                    "strategic_tier": vendor.strategic_tier,
                }

            portfolios[vendor_id]["products"].append(
                {
                    "id": product.id,
                    "name": product.name,
                    "family": getattr(product, "product_family_name", None),
                }
            )
            portfolios[vendor_id]["total_products"] += 1
            if product.base_license_cost_annual:
                portfolios[vendor_id]["total_cost"] += product.base_license_cost_annual

        return list(portfolios.values())

    def _analyze_technology_stacks(self, products: List[VendorProduct]) -> Dict:
        """Analyze technology stack distribution."""
        tech_distribution = {}

        for product in products:
            tech = product.primary_technology or "Unknown"
            if tech not in tech_distribution:
                tech_distribution[tech] = {"technology": tech, "product_count": 0, "products": []}

            tech_distribution[tech]["product_count"] += 1
            tech_distribution[tech]["products"].append(product.name)

        return {
            "distribution": list(tech_distribution.values()),
            "most_common": max(tech_distribution.values(), key=lambda x: x["product_count"])[
                "technology"
            ]
            if tech_distribution
            else None,
        }

    def _analyze_costs(self, products: List[VendorProduct]) -> Dict:
        """Analyze cost distribution."""
        costs = [
            float(p.base_license_cost_annual or 0) for p in products if p.base_license_cost_annual
        ]

        if not costs:
            return {"min": 0, "max": 0, "avg": 0, "median": 0, "total": 0}

        costs.sort()
        return {
            "min": costs[0],
            "max": costs[-1],
            "avg": sum(costs) / len(costs),
            "median": costs[len(costs) // 2],
            "total": sum(costs),
        }
