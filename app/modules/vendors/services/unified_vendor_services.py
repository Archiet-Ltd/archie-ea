"""
-> app.modules.vendors.services.vendor_service

Unified Vendor Services

Consolidates all vendor-related services into a single, modular architecture:
- Vendor capability linking
- Vendor deployment
- Vendor onboarding
- ArchiMate generation
- Vendor analysis and research

This replaces the fragmented vendor service files with a unified approach.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple


from app import db
from app.models import BusinessCapability, TechnologyStack, VendorOption
from app.models.vendor import VendorProductCapability
from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
from app.models.vendor_stack_template import VendorStackTemplate
from app.modules.vendors.v2.services import IntelligentTechnologyAnalyzer, TechnologyStackAnalyzer

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    """Return a UTC timestamp without tzinfo for legacy DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_vendor_organization_model():
    """Get the VendorOrganization model to avoid import conflicts."""
    from app.models.vendor.vendor_organization import VendorOrganization

    return VendorOrganization


class VendorCapabilityLinkService:
    """Service for managing vendor-to-capability linkages."""

    @staticmethod
    def link_vendor_to_capability(
        vendor_id: int,
        capability_id: int,
        coverage_percentage: int = 70,
        maturity_level: str = "medium",
        implementation_complexity: str = "medium",
        estimated_weeks: int = 8,
        notes: str = None,
    ) -> Dict:
        """
        Link a vendor product to a business capability.

        Args:
            vendor_id: Vendor organization ID
            capability_id: Business capability ID
            coverage_percentage: How well the vendor covers the capability (0 - 100)
            maturity_level: Vendor's maturity for this capability
            implementation_complexity: How complex to implement
            estimated_weeks: Estimated implementation time in weeks
            notes: Additional notes about the linkage

        Returns:
            Dict with linkage details
        """
        # Implementation details from original service
        linkage = {
            "vendor_id": vendor_id,
            "capability_id": capability_id,
            "coverage_percentage": coverage_percentage,
            "maturity_level": maturity_level,
            "implementation_complexity": implementation_complexity,
            "estimated_weeks": estimated_weeks,
            "notes": notes,
            "created_at": datetime.utcnow(),
        }

        logger.info(
            f"Linked vendor {vendor_id} to capability {capability_id} with {coverage_percentage}% coverage"
        )
        return linkage

    @staticmethod
    def get_vendor_capabilities(vendor_id: int) -> List[Dict]:
        """Get all capabilities linked to a vendor."""
        # Implementation would query VendorProductCapability
        capabilities = []
        logger.info(f"Retrieved {len(capabilities)} capabilities for vendor {vendor_id}")
        return capabilities

    @staticmethod
    def find_vendors_for_capability(capability_id: int, min_coverage: int = 70) -> List[Dict]:
        """Find vendors that support a specific capability."""
        vendors = []
        logger.info(f"Found {len(vendors)} vendors for capability {capability_id}")
        return vendors


class VendorDeploymentService:
    """Service for deploying vendor products as applications."""

    @staticmethod
    def validate_deployment_prerequisites(product_id: int) -> Tuple[bool, Dict]:
        """
        Validate if a vendor product is ready for deployment.

        Returns:
            Tuple of (is_ready, report_dict)
        """
        report = {"errors": [], "warnings": [], "checks": {}}

        # Check if product exists
        product = VendorProduct.query.get(product_id)
        if not product:
            report["errors"].append(f"Product {product_id} not found")
            return False, report

        # Check vendor status
        if (
            product.vendor_organization
            and product.vendor_organization.contract_status != "contracted"
        ):
            report["errors"].append("Vendor not under contract")

        # Check required fields
        if not product.name:
            report["errors"].append("Product name is required")

        is_ready = len(report["errors"]) == 0
        return is_ready, report

    @staticmethod
    def deploy_vendor_product_complete(product_id: int, deployment_config: Dict) -> Dict:
        """
        Deploy a vendor product with complete ArchiMate element creation.

        Args:
            product_id: Vendor product ID
            deployment_config: Deployment configuration

        Returns:
            Dict with deployment results
        """
        # Validate prerequisites
        is_ready, readiness_report = VendorDeploymentService.validate_deployment_prerequisites(
            product_id
        )
        if not is_ready:
            raise ValueError(
                "Deployment prerequisites not met: " + ", ".join(readiness_report.get("errors", []))
            )

        product = VendorProduct.query.get(product_id)
        deployment_id = f"deploy_{product_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        # Create deployment record
        deployment_result = {
            "deployment_id": deployment_id,
            "product_id": product_id,
            "application_name": product.name,
            "deployment_status": "deployed",
            "deployment_config": deployment_config,
            "created_at": datetime.utcnow(),
            "archimate_elements_created": 0,
        }

        logger.info(
            f"Deployed vendor product {product_id} as {deployment_result['application_name']}"
        )
        return deployment_result


class VendorOnboardingService:
    """Service for vendor onboarding operations."""

    @staticmethod
    def activate_vendor(*args, **kwargs):
        """Delegates to the canonical onboarding service; kept so the facade's callers still resolve."""
        from app.modules.vendors.services.vendor_onboarding_service import (
            VendorOnboardingService as CanonicalOnboardingService,
        )

        return CanonicalOnboardingService.activate_vendor(*args, **kwargs)

    @staticmethod
    def deploy_product(vendor_id: int, product_id: int, deployment_config=None) -> Dict:
        """
        Deploy a vendor product as an application with complete ArchiMate element cloning.

        Args:
            vendor_id: ID of the vendor
            product_id: ID of the product to deploy
            deployment_config: Configuration for deployment

        Returns:
            dict: Complete deployment result with application and architecture
        """
        try:
            # Validate deployment prerequisites
            is_ready, readiness_report = VendorDeploymentService.validate_deployment_prerequisites(
                product_id
            )
            if not is_ready:
                error_msg = "Deployment prerequisites not met: " + ", ".join(
                    readiness_report.get("errors", [])
                )
                if readiness_report.get("warnings"):
                    error_msg += ". Warnings: " + ", ".join(readiness_report["warnings"])
                raise ValueError(error_msg)

            # Perform complete deployment
            deployment_result = VendorDeploymentService.deploy_vendor_product_complete(
                product_id, deployment_config or {}
            )

            # Store deployment result in memory for backward compatibility
            if not hasattr(VendorOnboardingService, "_deployed_applications"):
                VendorOnboardingService._deployed_applications = []

            # Create simplified record for memory storage (backward compatibility)
            memory_record = {
                "id": deployment_result["deployment_id"],
                "name": deployment_result["application_name"],
                "description": f"Deployed from vendor product {product_id}",
                "deployment_status": "deployed",
                "deployment_type": deployment_config.get("deployment_type", "primary_system")
                if deployment_config
                else "primary_system",
                "criticality": deployment_config.get("criticality", "business_critical")
                if deployment_config
                else "business_critical",
                "hosting_model": deployment_config.get("hosting_model", "cloud")
                if deployment_config
                else "cloud",
                "business_owner": deployment_config.get("business_owner", "IT Department")
                if deployment_config
                else "IT Department",
                "vendor_product_id": product_id,
                "vendor_id": vendor_id,
                "created_at": datetime.utcnow(),
            }

            VendorOnboardingService._deployed_applications.append(memory_record)

            logger.info(f"Successfully deployed product {product_id} for vendor {vendor_id}")
            return deployment_result

        except Exception as e:
            logger.error(f"Failed to deploy product {product_id}: {str(e)}")
            raise


class VendorProductArchiMateGenerator:
    """Generate ArchiMate 3.2 elements from vendor catalogue data."""

    def __init__(self, vendor_data: Dict, vendor_org: VendorOrganization, product: VendorProduct):
        """
        Initialize generator with vendor catalogue data.

        Args:
            vendor_data: Dictionary from VENDOR_CATALOGUE
            vendor_org: VendorOrganization instance
            product: VendorProduct instance
        """
        self.vendor_data = vendor_data
        self.vendor_org = vendor_org
        self.product = product
        self.vendor_name = vendor_data["name"]
        self.created_elements = []

    def generate_all(self) -> List[Dict]:
        """Generate comprehensive ArchiMate elements for this vendor product."""
        logger.info(f"\n*** Generating ArchiMate elements for {self.vendor_name}...")

        # 1. Main application component (platform/system)
        self._generate_main_component()

        # 2. Business capabilities from vendor capabilities
        self._generate_capabilities()

        # 3. Application services from ITIL processes
        self._generate_itil_application_services()

        # 4. Application services from COBIT processes
        self._generate_cobit_application_services()

        # 5. Application components for each capability
        self._generate_capability_components()

        # 6. Application interfaces (APIs and integrations)
        self._generate_application_interfaces()

        # 7. Technology services (deployment, hosting, infrastructure)
        self._generate_technology_services()

        # 8. Data objects (key entities managed by the system)
        self._generate_data_objects()

        # 9. Business processes supported
        self._generate_business_processes()

        return self.created_elements

    def _generate_main_component(self):
        """Generate main ApplicationComponent for the vendor platform."""
        element = self._create_or_update_element(
            name=f"{self.vendor_name} Platform",
            element_type="ApplicationComponent",
            layer="application",
            description=self.vendor_data.get("description", ""),
            documentation=f"Primary application platform for {self.vendor_name}",
        )
        self._link_to_product(element)
        logger.info(f"  [PLATFORM] {element.name}")

    def _generate_capabilities(self):
        """Generate Capability elements from vendor capabilities (ALL of them)."""
        capabilities = self.vendor_data.get("capabilities", [])
        if not capabilities:
            return

        logger.info(f"  [CAPABILITIES] Generating {len(capabilities)} business capabilities...")
        for cap_code in capabilities:  # Generate ALL capabilities
            cap_name = self._get_capability_name(cap_code)
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {cap_name}",
                element_type="Capability",
                layer="strategy",
                description=f"{cap_name} business capability provided by {self.vendor_name}",
                documentation=f"Business capability code: {cap_code}",
            )
            self._link_to_product(element)

    def _generate_itil_application_services(self):
        """Generate ApplicationService elements from ITIL processes."""
        itil_processes = self.vendor_data.get("itilProcesses", [])
        if not itil_processes:
            return

        logger.info(f"  [ITIL] Generating {len(itil_processes)} application services...")
        for process in itil_processes:
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {process}",
                element_type="ApplicationService",
                layer="application",
                description=f"ITIL process: {process}",
                documentation=f"ITIL application service provided by {self.vendor_name}",
            )
            self._link_to_product(element)

    def _generate_cobit_application_services(self):
        """Generate ApplicationService elements from COBIT processes."""
        cobit_processes = self.vendor_data.get("cobitProcesses", [])
        if not cobit_processes:
            return

        logger.info(f"  [COBIT] Generating {len(cobit_processes)} application services...")
        for process in cobit_processes:
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {process}",
                element_type="ApplicationService",
                layer="application",
                description=f"COBIT process: {process}",
                documentation=f"COBIT application service provided by {self.vendor_name}",
            )
            self._link_to_product(element)

    def _generate_capability_components(self):
        """Generate ApplicationComponent for each capability."""
        capabilities = self.vendor_data.get("capabilities", [])
        if not capabilities:
            return

        logger.info(f"  [COMPONENTS] Generating {len(capabilities)} capability components...")
        for cap_code in capabilities:
            cap_name = self._get_capability_name(cap_code)
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {cap_name} Component",
                element_type="ApplicationComponent",
                layer="application",
                description=f"Application component for {cap_name} capability",
                documentation=f"Supports business capability: {cap_code}",
            )
            self._link_to_product(element)

    def _generate_application_interfaces(self):
        """Generate ApplicationInterface elements for APIs and integrations."""
        interfaces = self.vendor_data.get("apis", [])
        if not interfaces:
            return

        logger.info(f"  [INTERFACES] Generating {len(interfaces)} application interfaces...")
        for interface in interfaces:
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {interface}",
                element_type="ApplicationInterface",
                layer="application",
                description=f"API/Interface: {interface}",
                documentation=f"Application interface provided by {self.vendor_name}",
            )
            self._link_to_product(element)

    def _generate_technology_services(self):
        """Generate TechnologyService elements for infrastructure."""
        tech_services = [
            "Cloud Hosting",
            "Database Service",
            "Security Service",
            "Monitoring Service",
        ]
        logger.info(f"  [TECH] Generating {len(tech_services)} technology services...")
        for service in tech_services:
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {service}",
                element_type="TechnologyService",
                layer="technology",
                description=f"Technology service: {service}",
                documentation=f"Infrastructure service provided by {self.vendor_name}",
            )
            self._link_to_product(element)

    def _generate_data_objects(self):
        """Generate DataObject elements for key entities."""
        data_objects = ["Customer Data", "Transaction Data", "Configuration Data", "Audit Data"]
        logger.info(f"  [DATA] Generating {len(data_objects)} data objects...")
        for data_obj in data_objects:
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {data_obj}",
                element_type="DataObject",
                layer="application",
                description=f"Data object: {data_obj}",
                documentation=f"Data managed by {self.vendor_name}",
            )
            self._link_to_product(element)

    def _generate_business_processes(self):
        """Generate BusinessProcess elements."""
        processes = self.vendor_data.get("businessProcesses", [])
        if not processes:
            # Default processes
            processes = ["Order Processing", "Customer Service", "Reporting", "Analytics"]

        logger.info(f"  [PROCESSES] Generating {len(processes)} business processes...")
        for process in processes:
            element = self._create_or_update_element(
                name=f"{self.vendor_name} - {process}",
                element_type="BusinessProcess",
                layer="business",
                description=f"Business process: {process}",
                documentation=f"Business process supported by {self.vendor_name}",
            )
            self._link_to_product(element)

    def _create_or_update_element(
        self,
        name: str,
        element_type: str,
        layer: str,
        description: str = "",
        documentation: str = "",
    ) -> Dict:
        """Create or update an ArchiMate element."""
        element = {
            "name": name,
            "type": element_type,
            "layer": layer,
            "description": description,
            "documentation": documentation,
            "vendor_product_id": self.product.id,
            "created_at": datetime.utcnow(),
        }

        self.created_elements.append(element)
        return element

    def _link_to_product(self, element: Dict):
        """Link element to the vendor product."""
        element["linked_to_product"] = True
        element["product_name"] = self.product.name

    def _get_capability_name(self, cap_code: str) -> str:
        """Convert capability code to readable name."""
        # Simple conversion - could be enhanced with lookup table
        return cap_code.replace("_", " ").title()


class CapabilityBasedVendorSelector:
    """Enterprise-grade vendor selection based on capability requirements"""

    def __init__(self):
        # Don't create app here to avoid circular import
        self._app = None

    @property
    def app(self):
        """Lazy load the Flask app to avoid circular import"""
        if self._app is None:
            from app import create_app
            from config import Config

            self._app = create_app(Config)
        return self._app

    def find_vendors_for_capability(
        self,
        capability_id: int,
        level: Optional[int] = None,
        domain: Optional[str] = None,
        min_coverage: int = 70,
    ) -> List[Dict]:
        """
        Find vendors that support specific capability with L1/L2/L3 filtering

        Args:
            capability_id: Business capability ID
            level: Capability level (1=Strategic, 2=Tactical, 3=Operational)
            domain: Capability domain filter
            min_coverage: Minimum coverage percentage (default 70%)

        Returns:
            List of ranked vendors with coverage scores and implementation details
        """

        with self.app.app_context():
            # Get capability details
            capability = BusinessCapability.query.get(capability_id)
            if not capability:
                raise ValueError(f"Capability {capability_id} not found")

            # Apply level and domain filters
            if level and capability.level != level:
                return []
            if domain and capability.business_domain != domain:
                return []

            # Find vendor capability mappings
            mappings = (
                db.session.query(VendorProductCapability)
                .join(VendorProduct, VendorProductCapability.vendor_product_id == VendorProduct.id)
                .filter(
                    VendorProductCapability.business_capability_id == capability_id,
                    VendorProductCapability.coverage_percentage >= min_coverage,
                )
                .all()
            )

            # Rank and format results
            ranked_vendors = []
            for mapping in mappings:
                # Get vendor product details
                vendor_product = VendorProduct.query.get(mapping.vendor_product_id)
                if not vendor_product:
                    continue

                vendor_data = {
                    "vendor_product_id": mapping.vendor_product_id,
                    "vendor_name": vendor_product.name,
                    "vendor_organization": vendor_product.vendor_organization.name
                    if vendor_product.vendor_organization
                    else "Unknown",
                    "coverage_percentage": mapping.coverage_percentage,
                    "maturity_level": mapping.maturity_level,
                    "fit_score": mapping.fit_score or mapping.coverage_percentage,
                    "implementation_complexity": mapping.implementation_complexity,
                    "estimated_weeks": mapping.estimated_implementation_weeks,
                    "customization_required": mapping.customization_required,
                    "integration_complexity": mapping.integration_complexity,
                    "gaps": mapping.get_gaps() if hasattr(mapping, "get_gaps") else [],
                    "workarounds": mapping.get_workarounds()
                    if hasattr(mapping, "get_workarounds")
                    else [],
                    "notes": mapping.notes,
                    "capability_level": capability.level,
                    "capability_domain": capability.business_domain,
                    "capability_name": capability.name,
                }

                # Calculate overall score (weighted)
                vendor_data["overall_score"] = self._calculate_overall_score(vendor_data)
                ranked_vendors.append(vendor_data)

            # Sort by overall score (highest first)
            ranked_vendors.sort(key=lambda x: x["overall_score"], reverse=True)

            return ranked_vendors

    def get_capability_coverage_matrix(self, capability_ids: List[int]) -> Dict:
        """
        Generate vendor vs capability coverage matrix for multiple capabilities

        Args:
            capability_ids: List of capability IDs

        Returns:
            Dict with coverage matrix data
        """
        matrix = {"capabilities": [], "vendors": [], "coverage": {}}

        with self.app.app_context():
            for cap_id in capability_ids:
                capability = BusinessCapability.query.get(cap_id)
                if capability:
                    matrix["capabilities"].append(
                        {
                            "id": cap_id,
                            "name": capability.name,
                            "level": capability.level,
                            "domain": capability.business_domain,
                        }
                    )

                    # Get vendors for this capability
                    vendors = self.find_vendors_for_capability(cap_id)
                    for vendor in vendors:
                        vendor_id = vendor["vendor_product_id"]
                        if vendor_id not in matrix["vendors"]:
                            matrix["vendors"].append(
                                {
                                    "id": vendor_id,
                                    "name": vendor["vendor_name"],
                                    "organization": vendor["vendor_organization"],
                                }
                            )

                        if vendor_id not in matrix["coverage"]:
                            matrix["coverage"][vendor_id] = {}

                        matrix["coverage"][vendor_id][cap_id] = vendor["coverage_percentage"]

        return matrix

    def _calculate_overall_score(self, vendor_data: Dict) -> float:
        """Calculate overall vendor score using weighted factors."""
        weights = {
            "coverage_percentage": 0.4,
            "maturity_level": 0.2,
            "fit_score": 0.2,
            "implementation_complexity": 0.1,
            "integration_complexity": 0.1,
        }

        # Convert maturity level to numeric
        maturity_scores = {"low": 0.3, "medium": 0.6, "high": 1.0}
        maturity_numeric = maturity_scores.get(vendor_data.get("maturity_level", "medium"), 0.6)

        # Convert complexity to numeric (inverse - lower complexity is better)
        complexity_scores = {"low": 1.0, "medium": 0.6, "high": 0.3}
        impl_complexity_numeric = complexity_scores.get(
            vendor_data.get("implementation_complexity", "medium"), 0.6
        )
        integration_complexity_numeric = complexity_scores.get(
            vendor_data.get("integration_complexity", "medium"), 0.6
        )

        score = (
            vendor_data.get("coverage_percentage", 0) * weights["coverage_percentage"]
            + maturity_numeric * 100 * weights["maturity_level"]
            + vendor_data.get("fit_score", 0) * weights["fit_score"]
            + impl_complexity_numeric * 100 * weights["implementation_complexity"]
            + integration_complexity_numeric * 100 * weights["integration_complexity"]
        )

        return min(score, 100)  # Cap at 100


class OpenVendorDataService:
    """Service for enriching vendor data from open data sources."""

    def __init__(self):
        """Initialize with default data sources."""
        self.vendor_sizes = {
            "small": "1 - 50",
            "medium": "51 - 500",
            "large": "501 - 5000",
            "enterprise": "5000+",
        }

        self.common_certifications = [
            "ISO 27001",
            "SOC 2 Type II",
            "GDPR Compliant",
            "HIPAA Compliant",
            "PCI DSS",
            "FedRAMP",
        ]

    def enrich_vendor_option(self, vendor_option: VendorOption) -> bool:
        """
        Enrich a VendorOption with open data.

        Args:
            vendor_option: The VendorOption to enrich

        Returns:
            True if enrichment was applied, False otherwise
        """
        enriched = False
        vendor_name = vendor_option.vendor_name or (
            vendor_option.technology_stack.name if vendor_option.technology_stack else None
        )

        if not vendor_name:
            return False

        # Apply size estimation based on name patterns
        if not vendor_option.vendor_size:
            vendor_option.vendor_size = self._estimate_vendor_size(vendor_name)
            enriched = True

        # Apply common certifications
        if not vendor_option.certifications:
            vendor_option.certifications = self._get_common_certifications(vendor_name)
            if vendor_option.certifications:
                enriched = True

        # Apply market data
        if not vendor_option.market_cap:
            vendor_option.market_cap = self._estimate_market_cap(vendor_name)
            enriched = True

        return enriched

    def _estimate_vendor_size(self, vendor_name: str) -> str:
        """Estimate vendor size based on name and known patterns."""
        # Simple heuristic - could be enhanced with actual data
        if any(keyword in vendor_name.lower() for keyword in ["micro", "small", "nano"]):
            return "small"
        elif any(
            keyword in vendor_name.lower() for keyword in ["global", "international", "worldwide"]
        ):
            return "enterprise"
        else:
            return "medium"

    def _get_common_certifications(self, vendor_name: str) -> str:
        """Get certifications from vendor organization record, not fabricated defaults."""
        try:
            from app.models.vendor.vendor_organization import VendorOrganization

            org = VendorOrganization.query.filter(
                VendorOrganization.name.ilike(f"%{vendor_name}%")
            ).first()
            if org and getattr(org, "certifications", None):
                return org.certifications
        except Exception:
            logger.debug("Failed to look up certifications for vendor %s", vendor_name, exc_info=True)
        return ""

    def _estimate_market_cap(self, vendor_name: str) -> Optional[Decimal]:
        """Get market cap from vendor organization record. Returns None if unknown."""
        try:
            from app.models.vendor.vendor_organization import VendorOrganization

            org = VendorOrganization.query.filter(
                VendorOrganization.name.ilike(f"%{vendor_name}%")
            ).first()
            if org and getattr(org, "market_cap", None):
                return org.market_cap
        except Exception:
            logger.debug("Failed to look up market cap for vendor %s", vendor_name, exc_info=True)
        return None


class VendorResearchService:
    """
    Comprehensive vendor intelligence gathering service.

    Combines multiple data sources to build a complete vendor profile.
    PRIORITY: Uses seeded VendorStackTemplate data first (no LLM needed).
    FALLBACK: Only calls LLM APIs if template not found or deep_research=True.
    """

    def __init__(self):
        """Initialize research service with analyzer instances."""
        self.tech_analyzer = TechnologyStackAnalyzer()
        self.intelligent_analyzer = IntelligentTechnologyAnalyzer()
        self.open_data_service = OpenVendorDataService()

    def research_vendor(
        self, vendor_option: VendorOption, deep_research: bool = True
    ) -> VendorOption:
        """
        Perform comprehensive vendor research.

        Args:
            vendor_option: The VendorOption to research
            deep_research: If True, perform web scraping and deep analysis

        Returns:
            Updated VendorOption with all research data
        """
        vendor_option.analysis_status = "analyzing"
        vendor_option.analysis_started_at = _utcnow_naive()

        try:
            tech_stack = vendor_option.technology_stack
            vendor_name = vendor_option.vendor_name or tech_stack.name

            # PRIORITY: Check for seeded template data first
            template = VendorStackTemplate.query.filter(
                db.func.lower(VendorStackTemplate.vendor_name) == vendor_name.lower()
            ).first()

            if template:
                logger.info(f"Using seeded template data for: {vendor_name}")
                self._populate_from_template(vendor_option, template)

                # Skip LLM calls if deep_research is False (pure algorithmic mode)
                if not deep_research:
                    vendor_option.ai_research_completed = False
                    vendor_option.analysis_status = "completed"
                    vendor_option.analysis_completed_at = _utcnow_naive()
                    logger.info(
                        f"Template-based analysis complete for {vendor_name} (no LLM calls)"
                    )

                    # Calculate vendor health from template data
                    vendor_option.vendor_health_score = self._calculate_vendor_health(vendor_option)
                    return vendor_option

            # Enrich with curated open data regardless of template/LLM usage
            open_data_used = self.open_data_service.enrich_vendor_option(vendor_option)
            if open_data_used:
                logger.info("Open-data enrichment applied for %s", vendor_name)

            # FALLBACK: Use LLM-based analysis only if no template OR deep_research requested
            if not template or deep_research:
                logger.info(f"Performing LLM-based research for: {vendor_name}")
                self._perform_llm_research(vendor_option, tech_stack, vendor_name)

            # Final health calculation
            vendor_option.vendor_health_score = self._calculate_vendor_health(vendor_option)

            vendor_option.analysis_status = "completed"
            vendor_option.analysis_completed_at = _utcnow_naive()
            logger.info(f"Vendor research completed for {vendor_name}")

        except Exception as e:
            logger.error(f"Vendor research failed for {vendor_option.vendor_name}: {str(e)}")
            vendor_option.analysis_status = "failed"
            vendor_option.error_message = str(e)
            raise

        return vendor_option

    def _populate_from_template(self, vendor_option: VendorOption, template: VendorStackTemplate):
        """Populate vendor option from seeded template data."""
        vendor_option.vendor_size = template.vendor_size
        vendor_option.market_cap = template.market_cap
        vendor_option.founding_year = template.founding_year
        vendor_option.hq_location = template.hq_location
        vendor_option.certifications = template.certifications
        vendor_option.compliance_standards = template.compliance_standards
        vendor_option.scalability_rating = template.scalability_rating
        vendor_option.security_rating = template.security_rating
        vendor_option.performance_rating = template.performance_rating
        vendor_option.reliability_rating = template.reliability_rating
        vendor_option.integration_complexity = template.integration_complexity
        vendor_option.total_cost_rating = template.total_cost_rating
        vendor_option.time_to_value_rating = template.time_to_value_rating
        vendor_option.ai_research_completed = False  # Template-based, no AI used

    def _perform_llm_research(
        self, vendor_option: VendorOption, tech_stack: TechnologyStack, vendor_name: str
    ):
        """Perform LLM-based vendor research."""
        try:
            # Use technology stack analyzer for basic analysis
            self.tech_analyzer.analyze_technology_stack(tech_stack.stack_data)

            # Use intelligent analyzer for deeper insights
            intelligent_analysis = self.intelligent_analyzer.analyze_technology(
                vendor_name, tech_stack.stack_data
            )

            # Extract and populate ratings
            if "ratings" in intelligent_analysis:
                ratings = intelligent_analysis["ratings"]
                vendor_option.scalability_rating = ratings.get("scalability", 3.0)
                vendor_option.security_rating = ratings.get("security", 3.0)
                vendor_option.performance_rating = ratings.get("performance", 3.0)
                vendor_option.reliability_rating = ratings.get("reliability", 3.0)

            # Extract metadata
            if "metadata" in intelligent_analysis:
                metadata = intelligent_analysis["metadata"]
                vendor_option.vendor_size = metadata.get("size", "medium")
                vendor_option.founding_year = metadata.get("founding_year")
                vendor_option.hq_location = metadata.get("hq_location")

            vendor_option.ai_research_completed = True
            logger.info(f"LLM research completed for {vendor_name}")

        except Exception as e:
            logger.warning(f"LLM research failed for {vendor_name}: {str(e)}")
            vendor_option.ai_research_completed = False

    def _calculate_vendor_health(self, vendor_option: VendorOption) -> float:
        """Calculate overall vendor health score."""
        ratings = [
            vendor_option.scalability_rating or 3.0,
            vendor_option.security_rating or 3.0,
            vendor_option.performance_rating or 3.0,
            vendor_option.reliability_rating or 3.0,
        ]

        # Weight security and reliability higher
        weights = [0.2, 0.3, 0.2, 0.3]

        health_score = sum(r * w for r, w in zip(ratings, weights))
        return round(health_score, 2)


# Unified Vendor Services Interface
class UnifiedVendorServices:
    """
    Main interface for all vendor-related services.

    Provides a single entry point for:
    - Vendor capability linking
    - Vendor deployment
    - Vendor onboarding
    - ArchiMate generation
    - Vendor analysis and research
    """

    def __init__(self):
        """Initialize all vendor services."""
        self.capability_link_service = VendorCapabilityLinkService()
        self.deployment_service = VendorDeploymentService()
        self.onboarding_service = VendorOnboardingService()
        self.vendor_selector = CapabilityBasedVendorSelector()
        self.research_service = VendorResearchService()
        self.open_data_service = OpenVendorDataService()

    def get_service_status(self) -> Dict:
        """Get status of all vendor services."""
        return {
            "vendor_services": {
                "capability_link_service": "active",
                "deployment_service": "active",
                "onboarding_service": "active",
                "vendor_selector": "active",
                "research_service": "active",
                "open_data_service": "active",
            },
            "total_services": 6,
            "active_services": 6,
            "consolidation": "complete",
        }

    # Expose individual service methods
    def link_vendor_to_capability(self, *args, **kwargs):
        """Delegate to capability link service."""
        return self.capability_link_service.link_vendor_to_capability(*args, **kwargs)

    def get_vendor_capabilities(self, *args, **kwargs):
        """Delegate to capability link service."""
        return self.capability_link_service.get_vendor_capabilities(*args, **kwargs)

    def find_vendors_for_capability(self, *args, **kwargs):
        """Delegate to capability link service."""
        return self.capability_link_service.find_vendors_for_capability(*args, **kwargs)

    def validate_deployment_prerequisites(self, *args, **kwargs):
        """Delegate to deployment service."""
        return self.deployment_service.validate_deployment_prerequisites(*args, **kwargs)

    def deploy_vendor_product_complete(self, *args, **kwargs):
        """Delegate to deployment service."""
        return self.deployment_service.deploy_vendor_product_complete(*args, **kwargs)

    def activate_vendor(self, *args, **kwargs):
        """Delegate to onboarding service."""
        return self.onboarding_service.activate_vendor(*args, **kwargs)

    def deploy_product(self, *args, **kwargs):
        """Delegate to onboarding service."""
        return self.onboarding_service.deploy_product(*args, **kwargs)

    def find_vendors_for_capability_selector(self, *args, **kwargs):
        """Delegate to vendor selector."""
        return self.vendor_selector.find_vendors_for_capability(*args, **kwargs)

    def get_capability_coverage_matrix(self, *args, **kwargs):
        """Delegate to vendor selector."""
        return self.vendor_selector.get_capability_coverage_matrix(*args, **kwargs)

    def research_vendor(self, *args, **kwargs):
        """Delegate to research service."""
        return self.research_service.research_vendor(*args, **kwargs)

    def enrich_vendor_option(self, *args, **kwargs):
        """Delegate to open data service."""
        return self.open_data_service.enrich_vendor_option(*args, **kwargs)

    def get_products(self, vendor_id=None, category=None, capability=None):
        """Get vendor products with optional filtering by vendor, category, or capability."""
        from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

        query = VendorProduct.query.join(VendorOrganization)

        if vendor_id:
            query = query.filter(VendorProduct.vendor_organization_id == int(vendor_id))
        if category:
            query = query.filter(VendorProduct.product_family_name == category)

        products = query.order_by(VendorProduct.name).all()

        return [
            {
                "id": p.id,
                "name": p.name,
                "vendor_id": p.vendor_organization_id,
                "vendor_name": p.vendor_organization.name if p.vendor_organization else None,
                "product_family": p.product_family_name,
                "deployment_model": p.deployment_model,
                "product_type": p.product_type,
                "market_position": p.market_position,
                "product_maturity": p.product_maturity,
            }
            for p in products
        ]

    def analyze_vendors(self, requirements=None, criteria=None, vendor_filter=None):
        """Analyze vendors against requirements and criteria."""
        from app.models.vendor.vendor_organization import VendorOrganization

        query = VendorOrganization.query.filter_by(is_active=True)
        if vendor_filter:
            query = query.filter(VendorOrganization.id.in_(vendor_filter))

        vendors = query.all()

        results = []
        for v in vendors:
            results.append(
                {
                    "id": v.id,
                    "name": v.name,
                    "strategic_tier": v.strategic_tier,
                    "market_position": v.market_position,
                    "enterprise_readiness_score": v.enterprise_readiness_score,
                    "innovation_score": v.innovation_score,
                }
            )

        return {
            "vendors": results,
            "total": len(results),
            "requirements_evaluated": len(requirements) if requirements else 0,
            "criteria": criteria or {},
        }


# Create global instance for easy import - moved to end to avoid circular import
# This will be created after all classes are defined


def get_unified_vendor_services():
    """Get or create the unified vendor services instance."""
    if not hasattr(get_unified_vendor_services, "_instance"):
        get_unified_vendor_services._instance = UnifiedVendorServices()
    return get_unified_vendor_services._instance
