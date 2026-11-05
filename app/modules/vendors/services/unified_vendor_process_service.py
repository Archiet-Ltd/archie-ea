"""
-> app.modules.vendors.services.integration_service

Unified Vendor-Process-Capability Query Service

This service provides unified queries across vendors, APQC processes, and business capabilities
to support enterprise architecture analysis and decision-making.

Usage:
    from app.modules.vendors.services.unified_vendor_process_service import UnifiedVendorProcessService
    service = UnifiedVendorProcessService()

    # Find vendors for a specific APQC process
    vendors = service.get_vendors_for_apqc_process("8.1")

    # Find processes supported by a vendor
    processes = service.get_apqc_processes_for_vendor("servicenow")

    # Get capability-process-vendor triad analysis
    analysis = service.get_capability_process_vendor_analysis("customer-relationship-management")
"""

from typing import Dict, List


from app import db
from app.models.apqc_process import APQCProcess, process_code_sort_key
from app.models.business_capability import BusinessCapability
from app.models.capability_process_mapping import CapabilityProcessMapping
from app.models.vendor.vendor_organization import VendorOrganization
from app.models.vendor.vendor_product import VendorProduct
from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping


class UnifiedVendorProcessService:
    """Unified service for vendor-process-capability analysis"""

    def __init__(self):
        self.cache = {}

    def get_vendors_for_apqc_process(
        self, process_code: str, include_analysis: bool = True
    ) -> List[Dict]:
        """
        Get all vendors that support a specific APQC process

        Args:
            process_code: APQC process code (e.g., "8.1", "8.1.1")
            include_analysis: Whether to include detailed analysis

        Returns:
            List of vendor dictionaries with coverage details
        """
        # Get APQC process
        apqc_process = APQCProcess.query.filter_by(process_code=process_code).first()
        if not apqc_process:
            return []

        # Get all mappings for this process
        mappings = (
            db.session.query(VendorProductAPQCMapping)
            .join(VendorProduct)
            .join(VendorOrganization)
            .filter(VendorProductAPQCMapping.apqc_process_id == apqc_process.id)
            .all()
        )

        results = []
        for mapping in mappings:
            vendor_org = mapping.vendor_product.vendor_organization
            vendor_product = mapping.vendor_product

            vendor_data = {
                "vendor_id": vendor_org.vendor_id,
                "vendor_name": vendor_org.name,
                "product_name": vendor_product.name,
                "product_id": vendor_product.id,
                "market_position": vendor_org.market_position,
                "market_share": vendor_org.market_share,
                "coverage_percentage": mapping.coverage_percentage,
                "coverage_level": mapping.coverage_level,
                "implementation_complexity": mapping.implementation_complexity,
                "business_value": mapping.business_value,
                "strategic_fit": mapping.strategic_fit,
                "risk_level": mapping.risk_level,
                "architecture_domains": mapping.architecture_domains,
            }

            if include_analysis:
                vendor_data.update(
                    {
                        "integration_effort": mapping.integration_effort,
                        "technical_risk": mapping.technical_risk,
                        "competitive_advantage": mapping.competitive_advantage,
                        "gap_status": mapping.gap_status,
                        "gap_description": mapping.gap_description,
                        "implementation_cost": mapping.estimated_implementation_cost,
                        "expected_roi": mapping.expected_roi,
                        "vendor_maturity_score": mapping.vendor_maturity_score,
                        "compliance_frameworks": mapping.compliance_frameworks,
                        "security_posture": mapping.security_posture,
                        "implementation_notes": mapping.implementation_notes,
                        "evidence_urls": mapping.evidence_urls,
                    }
                )

            results.append(vendor_data)

        # Sort by coverage percentage and market position
        results.sort(
            key=lambda x: (
                -x["coverage_percentage"],
                {"LEADER": 3, "CHALLENGER": 2, "NICHE": 1}.get(x["market_position"], 0),
            )
        )

        return results

    def get_apqc_processes_for_vendor(
        self, vendor_id: str, include_analysis: bool = True
    ) -> List[Dict]:
        """
        Get all APQC processes supported by a specific vendor

        Args:
            vendor_id: Vendor identifier
            include_analysis: Whether to include detailed analysis

        Returns:
            List of process dictionaries with coverage details
        """
        # Get vendor organization
        vendor_org = VendorOrganization.query.filter_by(vendor_id=vendor_id).first()
        if not vendor_org:
            return []

        # Get all products for this vendor
        VendorProduct.query.filter_by(vendor_id=vendor_id).all()

        # Get all mappings for this vendor's products
        mappings = (
            db.session.query(VendorProductAPQCMapping)
            .join(VendorProduct)
            .join(APQCProcess)
            .filter(VendorProduct.vendor_id == vendor_id)
            .all()
        )

        results = []
        for mapping in mappings:
            apqc_process = mapping.apqc_process
            vendor_product = mapping.vendor_product

            process_data = {
                "process_code": apqc_process.process_code,
                "process_name": apqc_process.process_name,
                "level": apqc_process.level,
                "category": apqc_process.category,
                "architecture_domains": apqc_process.architecture_domains,
                "product_name": vendor_product.name,
                "product_id": vendor_product.id,
                "coverage_percentage": mapping.coverage_percentage,
                "coverage_level": mapping.coverage_level,
                "implementation_complexity": mapping.implementation_complexity,
                "business_value": mapping.business_value,
                "strategic_fit": mapping.strategic_fit,
                "gap_status": mapping.gap_status,
            }

            if include_analysis:
                process_data.update(
                    {
                        "integration_effort": mapping.integration_effort,
                        "technical_risk": mapping.technical_risk,
                        "competitive_advantage": mapping.competitive_advantage,
                        "gap_description": mapping.gap_description,
                        "implementation_cost": mapping.estimated_implementation_cost,
                        "expected_roi": mapping.expected_roi,
                        "security_posture": mapping.security_posture,
                        "implementation_notes": mapping.implementation_notes,
                        "last_assessment_date": mapping.last_assessment_date,
                    }
                )

            results.append(process_data)

        # Sort by process code and level
        results.sort(
            key=lambda x: (
                process_code_sort_key(x["process_code"]),
                x["level"] or 0,
                x["process_name"] or "",
                x["product_id"] or 0,
            )
        )

        return results

    def get_capability_process_vendor_analysis(
        self, capability_name: str = None, capability_id: int = None
    ) -> Dict:
        """
        Get comprehensive analysis for a business capability including:
        - Supported APQC processes
        - Vendor solutions for each process
        - Coverage gaps and recommendations

        Args:
            capability_name: Business capability name
            capability_id: Business capability ID

        Returns:
            Comprehensive analysis dictionary
        """
        # Get business capability
        if capability_id:
            capability = BusinessCapability.query.get(capability_id)
        elif capability_name:
            capability = BusinessCapability.query.filter_by(name=capability_name).first()
        else:
            return {"error": "Either capability_name or capability_id required"}

        if not capability:
            return {"error": "Capability not found"}

        # Get associated APQC processes
        process_mappings = (
            db.session.query(CapabilityProcessMapping)
            .join(APQCProcess)
            .filter(CapabilityProcessMapping.business_capability_id == capability.id)
            .all()
        )

        analysis = {
            "capability": {
                "id": capability.id,
                "name": capability.name,
                "description": capability.description,
                "level": capability.level,
                "domain": getattr(capability, "domain", None),
            },
            "processes": [],
            "vendor_coverage": {},
            "gaps": [],
            "recommendations": [],
            "statistics": {
                "total_processes": len(process_mappings),
                "covered_processes": 0,
                "vendor_solutions": 0,
                "high_coverage_vendors": 0,
            },
        }

        # Analyze each process
        for process_mapping in process_mappings:
            apqc_process = process_mapping.apqc_process

            # Get vendors for this process
            vendors = self.get_vendors_for_apqc_process(
                apqc_process.process_code, include_analysis=False
            )

            process_data = {
                "process_code": apqc_process.process_code,
                "process_name": apqc_process.process_name,
                "level": apqc_process.level,
                "category": apqc_process.category,
                "architecture_domains": apqc_process.architecture_domains,
                "support_level": process_mapping.support_level,
                "coverage_percentage": process_mapping.coverage_percentage,
                "business_importance": process_mapping.business_importance,
                "vendor_count": len(vendors),
                "vendors": vendors[:5],  # Top 5 vendors
            }

            analysis["processes"].append(process_data)

            # Update statistics
            if vendors:
                analysis["statistics"]["covered_processes"] += 1
                analysis["statistics"]["vendor_solutions"] += len(vendors)

                # Count high-coverage vendors
                high_coverage = [v for v in vendors if v["coverage_percentage"] >= 80]
                analysis["statistics"]["high_coverage_vendors"] += len(high_coverage)

            # Identify gaps
            if not vendors or max(v["coverage_percentage"] for v in vendors) < 50:
                analysis["gaps"].append(
                    {
                        "process_code": apqc_process.process_code,
                        "process_name": apqc_process.process_name,
                        "gap_type": "NO_VENDOR_COVERAGE" if not vendors else "LOW_COVERAGE",
                        "max_coverage": max(v["coverage_percentage"] for v in vendors)
                        if vendors
                        else 0,
                    }
                )

        # Generate recommendations
        analysis["recommendations"] = self._generate_capability_recommendations(analysis)

        return analysis

    def get_architecture_domain_analysis(self, domain: str) -> Dict:
        """
        Get analysis for a specific architecture domain

        Args:
            domain: Architecture domain (Enterprise, Business, Application, Solutions, Data, Integration)

        Returns:
            Domain-specific analysis
        """
        # APQCProcess has no architecture-domain column, so there is no schema
        # linkage from an architecture domain to APQC processes. Degrade gracefully
        # to an empty set rather than crash on a non-existent attribute (the old
        # `.architecture_domains.contains(...)` raised AttributeError).
        processes = []

        # Get all vendor mappings for these processes
        process_ids = [p.id for p in processes]
        mappings = VendorProductAPQCMapping.query.filter(
            VendorProductAPQCMapping.apqc_process_id.in_(process_ids)
        ).all()

        analysis = {
            "domain": domain,
            "process_count": len(processes),
            "vendor_mappings": len(mappings),
            "processes": [],
            "top_vendors": {},
            "coverage_summary": {
                "high_coverage": 0,
                "medium_coverage": 0,
                "low_coverage": 0,
                "no_coverage": 0,
            },
        }

        # Analyze each process
        for process in processes:
            process_mappings = [m for m in mappings if m.apqc_process_id == process.id]

            if process_mappings:
                avg_coverage = sum(m.coverage_percentage for m in process_mappings) / len(
                    process_mappings
                )
                max_coverage = max(m.coverage_percentage for m in process_mappings)
                vendor_count = len(set(m.vendor_product.vendor_id for m in process_mappings))
            else:
                avg_coverage = 0
                max_coverage = 0
                vendor_count = 0

            process_data = {
                "process_code": process.process_code,
                "process_name": process.process_name,
                "level": process.level,
                "category": process.category,
                "vendor_count": vendor_count,
                "avg_coverage": avg_coverage,
                "max_coverage": max_coverage,
            }

            analysis["processes"].append(process_data)

            # Update coverage summary
            if max_coverage >= 80:
                analysis["coverage_summary"]["high_coverage"] += 1
            elif max_coverage >= 50:
                analysis["coverage_summary"]["medium_coverage"] += 1
            elif max_coverage > 0:
                analysis["coverage_summary"]["low_coverage"] += 1
            else:
                analysis["coverage_summary"]["no_coverage"] += 1

        # Identify top vendors for this domain
        vendor_counts = {}
        for mapping in mappings:
            vendor_id = mapping.vendor_product.vendor_id
            vendor_counts[vendor_id] = vendor_counts.get(vendor_id, 0) + 1

        # Get vendor details for top vendors
        top_vendor_ids = sorted(vendor_counts.keys(), key=lambda x: vendor_counts[x], reverse=True)[
            :10
        ]

        for vendor_id in top_vendor_ids:
            vendor_org = VendorOrganization.query.filter_by(vendor_id=vendor_id).first()
            if vendor_org:
                analysis["top_vendors"][vendor_id] = {
                    "name": vendor_org.name,
                    "market_position": vendor_org.market_position,
                    "process_count": vendor_counts[vendor_id],
                }

        return analysis

    def find_vendors_for_capability_gap(
        self, capability_name: str, gap_process_code: str
    ) -> List[Dict]:
        """
        Find vendors that can fill a specific capability gap

        Args:
            capability_name: Business capability name
            gap_process_code: APQC process code with coverage gap

        Returns:
            List of recommended vendors with implementation analysis
        """
        # Get vendors for the gap process
        vendors = self.get_vendors_for_apqc_process(gap_process_code, include_analysis=True)

        # Filter and rank vendors based on suitability for the capability
        recommended_vendors = []

        for vendor in vendors:
            # Calculate suitability score
            score = self._calculate_vendor_suitability_score(
                vendor, capability_name, gap_process_code
            )

            vendor["suitability_score"] = score
            vendor["recommendation_reason"] = self._generate_recommendation_reason(
                vendor, capability_name, gap_process_code
            )

            recommended_vendors.append(vendor)

        # Sort by suitability score
        recommended_vendors.sort(key=lambda x: x["suitability_score"], reverse=True)

        return recommended_vendors[:5]  # Top 5 recommendations

    def get_process_capability_coverage_matrix(self) -> Dict:
        """
        Get a matrix showing process-to-capability coverage

        Returns:
            Coverage matrix with statistics
        """
        # Get all processes and capabilities
        processes = APQCProcess.query.all()
        capabilities = BusinessCapability.query.all()

        matrix = {
            "processes": [],
            "capabilities": [],
            "coverage_matrix": {},
            "statistics": {
                "total_processes": len(processes),
                "total_capabilities": len(capabilities),
                "total_mappings": 0,
                "avg_coverage": 0,
            },
        }

        # Build process list
        for process in processes:
            matrix["processes"].append(
                {
                    "code": process.process_code,
                    "name": process.process_name,
                    "level": process.level,
                    "category": process.category,
                }
            )

        # Build capability list
        for capability in capabilities:
            matrix["capabilities"].append(
                {"id": capability.id, "name": capability.name, "level": capability.level}
            )

        # Build coverage matrix
        total_coverage = 0
        mapping_count = 0

        for capability in capabilities:
            capability_mappings = CapabilityProcessMapping.query.filter_by(
                business_capability_id=capability.id
            ).all()

            for mapping in capability_mappings:
                process_code = mapping.apqc_process.process_code
                coverage = mapping.coverage_percentage

                matrix["coverage_matrix"][f"{capability.id}_{process_code}"] = {
                    "coverage": coverage,
                    "support_level": mapping.support_level,
                    "business_importance": mapping.business_importance,
                }

                total_coverage += coverage
                mapping_count += 1

        # Calculate statistics
        if mapping_count > 0:
            matrix["statistics"]["total_mappings"] = mapping_count
            matrix["statistics"]["avg_coverage"] = total_coverage / mapping_count

        return matrix

    # Helper methods
    def _generate_capability_recommendations(self, analysis: Dict) -> List[Dict]:
        """Generate recommendations based on capability analysis"""
        recommendations = []

        # Gap analysis recommendations
        if analysis["gaps"]:
            gap_count = len(analysis["gaps"])
            recommendations.append(
                {
                    "type": "GAP_ANALYSIS",
                    "priority": "HIGH",
                    "title": f"Address {gap_count} Process Coverage Gaps",
                    "description": f"{gap_count} processes have no or low vendor coverage. Consider vendor evaluation or custom development.",
                    "action_items": [
                        "Evaluate vendors for uncovered processes",
                        "Consider build vs buy analysis",
                        "Prioritize gaps by business impact",
                    ],
                }
            )

        # Vendor consolidation recommendations
        if analysis["statistics"]["vendor_solutions"] > 10:
            recommendations.append(
                {
                    "type": "VENDOR_CONSOLIDATION",
                    "priority": "MEDIUM",
                    "title": "Consider Vendor Consolidation",
                    "description": "Multiple vendors support this capability. Consider consolidation for better integration and cost management.",
                    "action_items": [
                        "Analyze vendor overlap",
                        "Evaluate consolidation benefits",
                        "Assess migration complexity",
                    ],
                }
            )

        # Strategic partnership recommendations
        high_coverage_vendors = analysis["statistics"]["high_coverage_vendors"]
        if high_coverage_vendors > 0:
            recommendations.append(
                {
                    "type": "STRATEGIC_PARTNERSHIP",
                    "priority": "LOW",
                    "title": "Strengthen Strategic Vendor Relationships",
                    "description": f"{high_coverage_vendors} vendors provide high coverage. Consider strategic partnerships.",
                    "action_items": [
                        "Identify strategic partnership opportunities",
                        "Negotiate enterprise agreements",
                        "Plan joint capability development",
                    ],
                }
            )

        return recommendations

    def _calculate_vendor_suitability_score(
        self, vendor: Dict, capability_name: str, process_code: str
    ) -> float:
        """Calculate suitability score for vendor recommendation"""
        score = 0.0

        # Coverage percentage (40% weight)
        score += (vendor["coverage_percentage"] / 100) * 0.4

        # Market position (20% weight)
        position_scores = {"LEADER": 1.0, "CHALLENGER": 0.7, "NICHE": 0.4}
        score += position_scores.get(vendor["market_position"], 0.3) * 0.2

        # Business value (15% weight)
        value_scores = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}
        score += value_scores.get(vendor["business_value"], 0.5) * 0.15

        # Strategic fit (15% weight)
        fit_scores = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}
        score += fit_scores.get(vendor["strategic_fit"], 0.5) * 0.15

        # Risk assessment (10% weight - lower risk is better)
        risk_scores = {"LOW": 1.0, "MEDIUM": 0.6, "HIGH": 0.3}
        score += risk_scores.get(vendor["risk_level"], 0.5) * 0.1

        return score

    def _generate_recommendation_reason(
        self, vendor: Dict, capability_name: str, process_code: str
    ) -> str:
        """Generate recommendation reason for vendor"""
        reasons = []

        if vendor["coverage_percentage"] >= 80:
            reasons.append(f"High coverage ({vendor['coverage_percentage']}%)")

        if vendor["market_position"] == "LEADER":
            reasons.append("Market leader with proven solutions")

        if vendor["business_value"] == "HIGH":
            reasons.append("High business value alignment")

        if vendor["strategic_fit"] == "HIGH":
            reasons.append("Strong strategic fit")

        if vendor["risk_level"] == "LOW":
            reasons.append("Low implementation risk")

        return "; ".join(reasons) if reasons else "Recommended based on overall assessment"
