"""
-> app.modules.vendors.services.seeder_service

APQC PCF and Vendor-APQC Mapping Seeding Service

This service seeds the APQC Process Classification Framework and creates
vendor-to-APQC process mappings for enterprise architecture analysis.

Usage:
    python -m app.services.seed_apqc_vendor_mapping
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import logging

from app import create_app, db
from app.models.apqc_process import APQCProcess
from app.models.business_capability import BusinessCapability
from app.models.unified_capability import (
    UnifiedCapabilityProcessMapping as CapabilityProcessMapping,
)
from app.models.vendor import VendorOrganization, VendorProduct
from app.models.vendor_product_apqc_mapping import VendorProductAPQCMapping
from app.seed_data.vendor_catalogue import APQC_PROCESSES, VENDOR_CATALOGUE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class APQCVendorSeedingService:
    """Service for seeding APQC processes and vendor mappings"""

    def __init__(self):
        self.app = create_app("default")
        self.processed_vendors = set()
        self.processed_mappings = set()

    def seed_all(self):
        """Seed all APQC data and vendor mappings"""
        with self.app.app_context():
            try:
                logger.info("Starting APQC and Vendor mapping seeding...")

                # Step 1: Seed APQC Processes
                self.seed_apqc_processes()

                # Step 2: Seed Vendor-APQC Mappings
                self.seed_vendor_apqc_mappings()

                # Step 3: Create Capability-Process Mappings
                self.seed_capability_process_mappings()

                # Step 4: Generate Strategic Analysis
                self.generate_strategic_analysis()

                logger.info("✅ APQC and Vendor mapping seeding completed successfully!")

            except Exception as e:
                logger.error(f"❌ Seeding failed: {str(e)}")
                raise

    def seed_apqc_processes(self):
        """Seed APQC PCF processes into database"""
        logger.info("Seeding APQC PCF processes...")

        processes_created = 0
        processes_updated = 0

        for process_code, process_data in APQC_PROCESSES.items():
            # Check if process already exists
            existing_process = APQCProcess.query.filter_by(process_code=process_code).first()

            if existing_process:
                # Update existing process
                existing_process.process_name = process_data["name"]
                existing_process.process_category = process_data.get("category", "Operational")
                existing_process.process_description = f"APQC {process_data['name']} - {process_data.get('category', 'Operational')} process"
                processes_updated += 1
            else:
                # Create new process
                process = APQCProcess(
                    process_code=process_code,
                    process_name=process_data["name"],
                    process_category=process_data.get("category", "Operational"),
                    process_description=f"APQC {process_data['name']} - {process_data.get('category', 'Operational')} process",
                )
                db.session.add(process)
                processes_created += 1

        db.session.commit()
        logger.info(f"✅ APQC Processes: {processes_created} created, {processes_updated} updated")

    def seed_vendor_apqc_mappings(self):
        """Seed vendor-to-APQC process mappings from vendor catalogue"""
        logger.info("Seeding Vendor-APQC mappings...")

        mappings_created = 0
        mappings_updated = 0

        for vendor_data in VENDOR_CATALOGUE:
            vendor_id = vendor_data["id"]

            # Get or create vendor organization
            vendor_org = VendorOrganization.query.filter_by(name=vendor_data["name"]).first()
            if not vendor_org:
                logger.warning(f"Vendor organization not found: {vendor_id}")
                continue

            # Get vendor products
            products = VendorProduct.query.filter_by(vendor_organization_id=vendor_org.id).all()
            if not products:
                logger.warning(f"No products found for vendor: {vendor_id}")
                continue

            # Get APQC processes for this vendor
            apqc_processes = vendor_data.get("apqcProcesses", [])
            if not apqc_processes:
                # Skip vendors without APQC mappings
                continue

            for product in products:
                for process_code in apqc_processes:
                    # Get APQC process
                    apqc_process = APQCProcess.query.filter_by(process_code=process_code).first()
                    if not apqc_process:
                        logger.warning(f"APQC process not found: {process_code}")
                        continue

                    # Check if mapping already exists
                    existing_mapping = VendorProductAPQCMapping.query.filter_by(
                        vendor_product_id=product.id, apqc_process_id=apqc_process.id
                    ).first()

                    if existing_mapping:
                        # Update existing mapping
                        self._update_mapping_strength(existing_mapping, vendor_data)
                        mappings_updated += 1
                    else:
                        # Create new mapping
                        mapping = self._create_vendor_apqc_mapping(
                            product, apqc_process, vendor_data
                        )
                        db.session.add(mapping)
                        mappings_created += 1

        db.session.commit()
        logger.info(
            f"✅ Vendor-APQC Mappings: {mappings_created} created, {mappings_updated} updated"
        )

    def _create_vendor_apqc_mapping(self, product, apqc_process, vendor_data):
        """Create a new vendor-APQC mapping with analysis data"""
        mapping = VendorProductAPQCMapping(
            vendor_product_id=product.id,
            apqc_process_id=apqc_process.id,
            # Coverage Analysis
            coverage_percentage=self._calculate_coverage_percentage(vendor_data, apqc_process),
            coverage_level=self._determine_coverage_level(vendor_data, apqc_process),
            # Implementation Analysis
            implementation_complexity=self._assess_implementation_complexity(
                vendor_data, apqc_process
            ),
            integration_effort=self._estimate_integration_effort(vendor_data, apqc_process),
            technical_risk=self._assess_technical_risk(vendor_data),
            # Strategic Analysis
            business_value=self._assess_business_value(vendor_data, apqc_process),
            strategic_fit=self._assess_strategic_fit(vendor_data, apqc_process),
            competitive_advantage=self._assess_competitive_advantage(vendor_data),
            # Gap Analysis
            gap_status=self._determine_gap_status(vendor_data, apqc_process),
            gap_description=self._generate_gap_description(vendor_data, apqc_process),
            # Evidence and Documentation
            evidence_urls=self._extract_evidence_urls(vendor_data),
            implementation_notes=self._generate_implementation_notes(vendor_data, apqc_process),
            # Compliance and Security
            compliance_frameworks=vendor_data.get("complianceFrameworks", []),
            security_posture=self._assess_security_posture(vendor_data),
            # Cost and ROI
            estimated_implementation_cost=self._estimate_implementation_cost(vendor_data),
            expected_roi=self._calculate_expected_roi(vendor_data, apqc_process),
            # Vendor Assessment
            vendor_maturity_score=self._assess_vendor_maturity(vendor_data),
            market_position=vendor_data.get("marketPosition", "CHALLENGER"),
            risk_level=vendor_data.get("riskLevel", "MEDIUM"),
            # Architecture Domain Mapping
            architecture_domains=self._map_architecture_domains(apqc_process),
            capability_support_level=self._assess_capability_support(vendor_data, apqc_process),
            # Metadata
            mapping_confidence="HIGH"
            if vendor_data.get("marketPosition") == "LEADER"
            else "MEDIUM",
            last_assessment_date=db.func.current_date(),
            data_source="VENDOR_CATALOGUE_ANALYSIS",
        )

        return mapping

    def _update_mapping_strength(self, mapping, vendor_data):
        """Update existing mapping with current vendor data"""
        mapping.coverage_percentage = self._calculate_coverage_percentage(
            vendor_data, mapping.apqc_process
        )
        mapping.market_position = vendor_data.get("marketPosition", mapping.market_position)
        mapping.risk_level = vendor_data.get("riskLevel", mapping.risk_level)
        mapping.last_assessment_date = db.func.current_date()

    def seed_capability_process_mappings(self):
        """Create mappings between business capabilities and APQC processes"""
        logger.info("Seeding Capability-Process mappings...")

        mappings_created = 0

        # Get all business capabilities
        capabilities = BusinessCapability.query.all()

        # Get all APQC processes
        apqc_processes = APQCProcess.query.all()

        for capability in capabilities:
            for apqc_process in apqc_processes:
                # Check if mapping should exist based on semantic similarity
                if self._should_map_capability_to_process(capability, apqc_process):
                    # Check if mapping already exists
                    existing_mapping = CapabilityProcessMapping.query.filter_by(
                        business_capability_id=capability.id, apqc_process_id=apqc_process.id
                    ).first()

                    if not existing_mapping:
                        # Create new mapping
                        mapping = CapabilityProcessMapping(
                            business_capability_id=capability.id,
                            apqc_process_id=apqc_process.id,
                            support_level=self._determine_support_level(capability, apqc_process),
                            coverage_percentage=self._calculate_capability_coverage(
                                capability, apqc_process
                            ),
                            business_importance=self._assess_business_importance(capability),
                            implementation_complexity=self._assess_process_complexity(apqc_process),
                        )
                        db.session.add(mapping)
                        mappings_created += 1

        db.session.commit()
        logger.info(f"✅ Capability-Process Mappings: {mappings_created} created")

    def generate_strategic_analysis(self):
        """Generate strategic analysis reports"""
        logger.info("Generating strategic analysis...")

        # Count by process category
        category_analysis = {}
        for process in APQCProcess.query.all():
            category = process.process_category or "Unknown"
            if category not in category_analysis:
                category_analysis[category] = {"processes": 0, "vendors": 0, "coverage": 0}
            category_analysis[category]["processes"] += 1

        # Count vendor coverage by category
        for mapping in VendorProductAPQCMapping.query.all():
            if mapping.apqc_process and mapping.apqc_process.process_category:
                category = mapping.apqc_process.process_category
                if category in category_analysis:
                    category_analysis[category]["vendors"] += 1
                    category_analysis[category]["coverage"] += mapping.coverage_percentage

        logger.info("📊 Strategic Analysis Summary:")
        for category, stats in category_analysis.items():
            avg_coverage = stats["coverage"] / max(stats["vendors"], 1)
            logger.info(
                f"  {category}: {stats['processes']} processes, {stats['vendors']} vendor mappings, {avg_coverage:.1f}% avg coverage"
            )

    # Helper methods for analysis
    def _calculate_coverage_percentage(self, vendor_data, apqc_process):
        """Calculate coverage percentage based on vendor capabilities"""
        base_coverage = 70  # Base coverage for listed processes

        # Increase for market leaders
        if vendor_data.get("marketPosition") == "LEADER":
            base_coverage += 15
        elif vendor_data.get("marketPosition") == "CHALLENGER":
            base_coverage += 5

        # Increase for comprehensive compliance frameworks
        frameworks = vendor_data.get("complianceFrameworks", [])
        if len(frameworks) > 3:
            base_coverage += 10

        return min(base_coverage, 95)

    def _determine_coverage_level(self, vendor_data, apqc_process):
        """Determine coverage level (FULL, PARTIAL, MINIMAL)"""
        coverage = self._calculate_coverage_percentage(vendor_data, apqc_process)
        if coverage >= 80:
            return "FULL"
        elif coverage >= 50:
            return "PARTIAL"
        else:
            return "MINIMAL"

    def _assess_implementation_complexity(self, vendor_data, apqc_process):
        """Assess implementation complexity"""
        deployment_models = vendor_data.get("deploymentModel", [])
        if "CLOUD" in deployment_models:
            return "LOW"
        elif "ON_PREMISE" in deployment_models:
            return "HIGH"
        else:
            return "MEDIUM"

    def _estimate_integration_effort(self, vendor_data, apqc_process):
        """Estimate integration effort in person-days"""
        integrations = vendor_data.get("integrations", [])
        base_effort = 30  # Base effort

        # Reduce effort for existing integrations
        if len(integrations) > 5:
            base_effort -= 15
        elif len(integrations) > 2:
            base_effort -= 8

        return max(base_effort, 5)

    def _assess_technical_risk(self, vendor_data):
        """Assess technical risk level"""
        risk_level = vendor_data.get("riskLevel", "MEDIUM")
        if risk_level == "LOW":
            return "LOW"
        elif risk_level == "HIGH":
            return "HIGH"
        else:
            return "MEDIUM"

    def _assess_business_value(self, vendor_data, apqc_process):
        """Assess business value (HIGH, MEDIUM, LOW)"""
        market_position = vendor_data.get("marketPosition", "CHALLENGER")
        if market_position == "LEADER":
            return "HIGH"
        elif market_position == "CHALLENGER":
            return "MEDIUM"
        else:
            return "LOW"

    def _assess_strategic_fit(self, vendor_data, apqc_process):
        """Assess strategic fit"""
        # Check if vendor's capabilities align with process category
        capabilities = vendor_data.get("capabilities", [])
        process_category = apqc_process.category

        # High fit for IT processes with security/compliance capabilities
        if process_category == "Support" and any(
            cap in capabilities for cap in ["security-management", "compliance-management"]
        ):
            return "HIGH"

        return "MEDIUM"

    def _assess_competitive_advantage(self, vendor_data):
        """Assess competitive advantage"""
        market_position = vendor_data.get("marketPosition", "CHALLENGER")
        market_share = vendor_data.get("marketShare", "MODERATE")

        if market_position == "LEADER" and market_share in ["MAJOR", "DOMINANT"]:
            return "HIGH"
        elif market_position == "CHALLENGER":
            return "MEDIUM"
        else:
            return "LOW"

    def _determine_gap_status(self, vendor_data, apqc_process):
        """Determine gap status"""
        coverage = self._calculate_coverage_percentage(vendor_data, apqc_process)
        if coverage >= 80:
            return "FULLY_COVERED"
        elif coverage >= 50:
            return "PARTIALLY_COVERED"
        else:
            return "GAP"

    def _generate_gap_description(self, vendor_data, apqc_process):
        """Generate gap description"""
        coverage = self._calculate_coverage_percentage(vendor_data, apqc_process)
        vendor_name = vendor_data["name"]

        if coverage >= 80:
            return f"{vendor_name} provides comprehensive coverage for {apqc_process.process_name}"
        elif coverage >= 50:
            return f"{vendor_name} provides partial coverage for {apqc_process.process_name} - some gaps exist"
        else:
            return f"{vendor_name} has minimal coverage for {apqc_process.process_name} - significant gaps"

    def _extract_evidence_urls(self, vendor_data):
        """Extract evidence URLs from vendor data"""
        urls = []
        website = vendor_data.get("website")
        if website:
            urls.append(website)
        return urls

    def _generate_implementation_notes(self, vendor_data, apqc_process):
        """Generate implementation notes"""
        deployment_models = vendor_data.get("deploymentModel", [])
        integrations = vendor_data.get("integrations", [])

        notes = f"Deploy via {', '.join(deployment_models)}. "
        if integrations:
            notes += f"Integrates with {', '.join(integrations[:5])}"

        return notes

    def _assess_security_posture(self, vendor_data):
        """Assess security posture"""
        frameworks = vendor_data.get("complianceFrameworks", [])
        if len(frameworks) >= 4:
            return "STRONG"
        elif len(frameworks) >= 2:
            return "MODERATE"
        else:
            return "BASIC"

    def _estimate_implementation_cost(self, vendor_data):
        """Estimate implementation cost"""
        cost_range = vendor_data.get("typicalAnnualCost", "50K-200K")
        return cost_range

    def _calculate_expected_roi(self, vendor_data, apqc_process):
        """Calculate expected ROI"""
        business_value = self._assess_business_value(vendor_data, apqc_process)
        if business_value == "HIGH":
            return "HIGH"
        elif business_value == "MEDIUM":
            return "MEDIUM"
        else:
            return "LOW"

    def _assess_vendor_maturity(self, vendor_data):
        """Assess vendor maturity score (1 - 5)"""
        market_position = vendor_data.get("marketPosition", "CHALLENGER")
        market_share = vendor_data.get("marketShare", "MODERATE")

        if market_position == "LEADER" and market_share == "DOMINANT":
            return 5
        elif market_position == "LEADER":
            return 4
        elif market_position == "CHALLENGER":
            return 3
        else:
            return 2

    def _map_architecture_domains(self, apqc_process):
        """Map architecture domains from APQC process"""
        # Simple mapping based on process category
        category_domains = {
            "Strategic": ["Enterprise", "Business"],
            "Operational": ["Business", "Application"],
            "Support": ["Application", "Data", "Integration"],
        }
        return category_domains.get(apqc_process.process_category, ["Business"])

    def _assess_capability_support(self, vendor_data, apqc_process):
        """Assess capability support level"""
        return self._determine_coverage_level(vendor_data, apqc_process)

    def _should_map_capability_to_process(self, capability, apqc_process):
        """Determine if capability should be mapped to process"""
        # Simple heuristic based on name similarity and domain
        capability_name = capability.name.lower()
        process_name = apqc_process.process_name.lower()

        # Check for common keywords
        common_keywords = ["management", "service", "process", "system", "application", "data"]
        capability_keywords = [word for word in common_keywords if word in capability_name]
        process_keywords = [word for word in common_keywords if word in process_name]

        # Map if they share keywords or are in related domains
        if capability_keywords and process_keywords:
            return True

        # Map based on architecture domain alignment
        if hasattr(capability, "domain") and capability.domain in apqc_process.architecture_domains:
            return True

        return False

    def _determine_support_level(self, capability, apqc_process):
        """Determine support level for capability-process mapping"""
        return "MEDIUM"  # Default for auto-generated mappings

    def _calculate_capability_coverage(self, capability, apqc_process):
        """Calculate capability coverage percentage"""
        return 60  # Default for auto-generated mappings

    def _assess_business_importance(self, capability):
        """Assess business importance of capability"""
        return "HIGH"  # Default for auto-generated mappings

    def _assess_process_complexity(self, apqc_process):
        """Assess process complexity"""
        if apqc_process.level == 1:
            return "HIGH"
        elif apqc_process.level == 2:
            return "MEDIUM"
        else:
            return "LOW"


def main():
    """Main execution function"""
    service = APQCVendorSeedingService()
    service.seed_all()


if __name__ == "__main__":
    main()
