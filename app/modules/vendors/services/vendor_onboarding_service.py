"""
-> app.modules.vendors.services.vendor_service

Vendor Onboarding Service for vendor activation and management.

Implements flask-base-master deploy flow: create ArchiMateElement + ApplicationComponent
+ application_vendor_products link as the canonical way to deploy vendor products.
"""

import logging
from datetime import datetime

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


# Import VendorOrganization directly to avoid conflicts
def get_vendor_organization_model():
    """Get the VendorOrganization model to avoid import conflicts."""
    from app.models.vendor.vendor_organization import VendorOrganization

    return VendorOrganization


class VendorOnboardingService:
    """Service for vendor onboarding operations."""

    @staticmethod
    def activate_vendor(
        vendor_id, contract_start_date=None, contract_end_date=None, contract_value=None
    ):
        """
        Activate a vendor from catalog to contracted status.

        Args:
            vendor_id: ID of the vendor to activate
            contract_start_date: Start date of the contract
            contract_end_date: End date of the contract
            contract_value: Annual contract value

        Returns:
            The activated vendor object
        """
        VendorOrganization = get_vendor_organization_model()
        vendor = VendorOrganization.query.get(vendor_id)
        if not vendor:
            raise ValueError(f"Vendor with ID {vendor_id} not found")

        # Update vendor contract information
        vendor.contract_status = "contracted"
        vendor.contract_start_date = contract_start_date
        vendor.contract_end_date = contract_end_date
        vendor.contract_value_annual = contract_value
        vendor.status = "active"

        # Set activation timestamp
        vendor.updated_at = datetime.utcnow()

        try:
            db.session.commit()
            return vendor
        except Exception as e:
            db.session.rollback()
            raise e

    @staticmethod
    def deploy_product(vendor_id, product_id, deployment_config=None):
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
            from app.modules.vendors.services.vendor_deployment_service import (
                VendorDeploymentService,
            )

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
                "application_id": deployment_result["application_id"],
                "deployed_at": datetime.utcnow(),
                "archimate_elements_count": deployment_result["results"]["archimate_cloning"][
                    "elements_cloned"
                ],
                "deployment_complete": True,
            }

            VendorOnboardingService._deployed_applications.append(memory_record)

            logger.info(
                f"Complete deployment successful: {deployment_result['application_name']} with {deployment_result['results']['archimate_cloning']['elements_cloned']} ArchiMate elements"
            )

            return deployment_result

        except Exception as e:
            logger.error(f"Error in vendor product deployment: {str(e)}")
            raise e

    @staticmethod
    def get_deployed_applications():
        """Get all deployed applications for display"""
        if not hasattr(VendorOnboardingService, "_deployed_applications"):
            return []
        return VendorOnboardingService._deployed_applications

    @staticmethod
    def deploy_vendor_product_as_application(
        vendor_product_id,
        application_name,
        description=None,
        deployment_type="primary_system",
        criticality="business_critical",
        hosting_model="cloud",
        business_owner=None,
    ):
        """
        Deploy a vendor product as an application instance (flask-base-master canonical flow).

        Creates ArchiMateElement, ApplicationComponent, and application_vendor_products link.
        Does NOT require template ArchiMate elements. Optional architecture clone is non-blocking.

        Args:
            vendor_product_id: VendorProduct ID to deploy
            application_name: Name for the deployed application instance
            description: Application description
            deployment_type: primary_system, integration_layer, supporting_tool, reporting_analytics
            criticality: mission_critical, business_critical, business_operational, administrative
            hosting_model: cloud, on_premise, hybrid, managed_service
            business_owner: Business owner name

        Returns:
            ApplicationComponent: Created application instance
        """
        from app.models.application_portfolio import ApplicationComponent
        from app.models.models import ArchiMateElement

        from app.models.vendor.vendor_organization import VendorProduct

        product = VendorProduct.query.get_or_404(vendor_product_id)
        vendor = product.vendor_organization

        # Check for duplicate application name
        existing = ApplicationComponent.query.filter_by(name=application_name).first()
        if existing:
            raise ValueError(f"Application '{application_name}' already exists")

        # 1. Create the ApplicationComponent; the shared flush listener (the
        # one element writer) creates its ArchiMate element through
        # sync_archimate_element, so a vendor-deployed application carries the
        # same name, description and provenance as a backfilled one.
        deployment_status = "production" if deployment_type == "primary_system" else "development"
        app_component = ApplicationComponent(
            name=application_name,
            description=description or f"Deployed instance of {product.name} from {vendor.name}",
            component_type=deployment_type,
            business_criticality=criticality,
            deployment_model=hosting_model,
            business_owner=business_owner,
            deployment_status=deployment_status,
        )
        db.session.add(app_component)
        db.session.flush()

        archimate_element = db.session.get(
            ArchiMateElement, app_component.archimate_element_id
        )
        if archimate_element is None:
            raise RuntimeError(
                f"The shared flush listener did not create an ArchiMate element "
                f"for deployed application '{application_name}'. The one-writer "
                "doctrine has no fallback here; the deployment is refused rather "
                "than silently writing an unmirrored row."
            )

        # 2. Keep the vendor/product context on the element the shared writer
        #    produced (display metadata, not the node identity).
        archimate_element.documentation = (
            f"Vendor: {vendor.name}\nProduct: {product.name}\nOwner: {business_owner or 'TBD'}"
        )
        archimate_element.status = "active"

        # 3. Link to vendor product via application_vendor_products
        db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                """
                INSERT INTO application_vendor_products
                (archimate_element_id, vendor_product_id, deployment_type, criticality, hosting_model, implementation_date)
                VALUES (:elem_id, :prod_id, :deploy, :crit, :host, :impl_date)
                """
            ),
            {
                "elem_id": archimate_element.id,
                "prod_id": product.id,
                "deploy": deployment_type,
                "crit": criticality,
                "host": hosting_model,
                "impl_date": datetime.utcnow(),
            },
        )

        # 4. Update vendor contract_status if contracted
        if vendor.contract_status == "contracted":
            vendor.contract_status = "deployed"

        db.session.commit()

        logger.info(
            f"Deployed vendor product {product.name} as application {application_name} (id={app_component.id})"
        )
        return app_component
