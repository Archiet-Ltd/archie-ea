"""
-> app.modules.vendors.services.seeder_service

Capability-to-Vendor/Application Mapping Seeder Services

Provides seeding for:
1. TechnicalCapability ↔ VendorProduct mappings
2. UnifiedCapability ↔ ApplicationComponent mappings
3. UnifiedCapability ↔ VendorOrganization (strategic) mappings
4. ApplicationComponent ↔ VendorProduct (technology stack) mappings

All services follow two-pass idempotent pattern:
- Pass 1: Insert or identify existing records
- Pass 2: Create relationships if they don't exist
"""


from app import db
from app.models import (
    ApplicationComponent,
    TechnicalCapability,
    UnifiedCapability,
    VendorOrganization,
    VendorProduct,
)
from app.models.capability_to_vendor_mapping import (
    ApplicationVendorProductMapping,
    TechnicalCapabilityVendorMapping,
    UnifiedCapabilityApplicationMapping,
    UnifiedCapabilityVendorOrganizationMapping,
)


class CapabilityVendorMappingSeeder:
    """
    Seeds TechnicalCapability ↔ VendorProduct mappings.

    Example: Which vendors implement "Application Modernization"?
    - Microsoft (Azure services)
    - Amazon (AWS services)
    - Google (GCP services)
    """

    @staticmethod
    def seed():
        """Seed technical capability to vendor product mappings."""

        mappings = [
            # Cloud Infrastructure vendors
            {
                "technical_capability_name": "Application Modernization",
                "vendor_product_name": "Microsoft Azure",
                "coverage_percentage": 90.0,
                "maturity_level": 5,
                "fit_score": 92.0,
                "implementation_effort": "low",
                "time_to_value_days": 14,
                "roi_percentage": 85.0,
            },
            {
                "technical_capability_name": "Application Modernization",
                "vendor_product_name": "AWS Elastic Beanstalk",
                "coverage_percentage": 88.0,
                "maturity_level": 5,
                "fit_score": 90.0,
                "implementation_effort": "low",
                "time_to_value_days": 14,
                "roi_percentage": 85.0,
            },
            # API Management
            {
                "technical_capability_name": "API Management",
                "vendor_product_name": "MuleSoft Anypoint",
                "coverage_percentage": 95.0,
                "maturity_level": 5,
                "fit_score": 95.0,
                "implementation_effort": "medium",
                "time_to_value_days": 30,
                "roi_percentage": 80.0,
            },
            {
                "technical_capability_name": "API Management",
                "vendor_product_name": "Apigee API Platform",
                "coverage_percentage": 92.0,
                "maturity_level": 5,
                "fit_score": 92.0,
                "implementation_effort": "medium",
                "time_to_value_days": 30,
                "roi_percentage": 78.0,
            },
            # Data Analytics
            {
                "technical_capability_name": "Advanced Analytics",
                "vendor_product_name": "Tableau",
                "coverage_percentage": 85.0,
                "maturity_level": 5,
                "fit_score": 88.0,
                "implementation_effort": "medium",
                "time_to_value_days": 45,
                "roi_percentage": 75.0,
            },
            {
                "technical_capability_name": "Advanced Analytics",
                "vendor_product_name": "Power BI",
                "coverage_percentage": 82.0,
                "maturity_level": 4,
                "fit_score": 85.0,
                "implementation_effort": "low",
                "time_to_value_days": 21,
                "roi_percentage": 78.0,
            },
        ]

        created = 0
        skipped = 0

        for mapping_data in mappings:
            try:
                # Find technical capability
                tech_cap = TechnicalCapability.query.filter_by(
                    name=mapping_data["technical_capability_name"]
                ).first()

                if not tech_cap:
                    print(
                        f"  ⚠️  TechnicalCapability not found: {mapping_data['technical_capability_name']}"
                    )
                    skipped += 1
                    continue

                # Find vendor product
                vendor_prod = VendorProduct.query.filter_by(
                    name=mapping_data["vendor_product_name"]
                ).first()

                if not vendor_prod:
                    print(f"  ⚠️  VendorProduct not found: {mapping_data['vendor_product_name']}")
                    skipped += 1
                    continue

                # Check if mapping already exists
                existing = TechnicalCapabilityVendorMapping.query.filter_by(
                    technical_capability_id=tech_cap.id, vendor_product_id=vendor_prod.id
                ).first()

                if existing:
                    skipped += 1
                    continue

                # Create mapping
                mapping = TechnicalCapabilityVendorMapping(
                    technical_capability_id=tech_cap.id,
                    vendor_product_id=vendor_prod.id,
                    coverage_percentage=mapping_data.get("coverage_percentage", 75.0),
                    maturity_level=mapping_data.get("maturity_level", 3),
                    fit_score=mapping_data.get("fit_score", 75.0),
                    implementation_effort=mapping_data.get("implementation_effort"),
                    time_to_value_days=mapping_data.get("time_to_value_days"),
                    roi_percentage=mapping_data.get("roi_percentage"),
                )

                db.session.add(mapping)
                created += 1

            except Exception as e:
                print(f"  ❌ Error creating mapping: {e}")
                db.session.rollback()

        try:
            db.session.commit()
            print(
                f"✅ TechnicalCapability → VendorProduct mappings: {created} created, {skipped} skipped"
            )
        except Exception as e:
            print(f"❌ Error committing mappings: {e}")
            db.session.rollback()


class CapabilityApplicationMappingSeeder:
    """
    Seeds UnifiedCapability ↔ ApplicationComponent mappings.

    Example: Which applications implement "Customer Management"?
    - Salesforce CRM
    - Microsoft Dynamics 365
    - SAP C/4HANA
    """

    @staticmethod
    def seed():
        """Seed unified capability to application mappings."""

        mappings = [
            # Customer Management implementations
            {
                "unified_capability_name": "Customer Management",
                "application_name": "Salesforce CRM",
                "support_level": "full",
                "coverage_percentage": 95.0,
                "functional_fit_score": 95.0,
                "health_status": "healthy",
                "strategic_alignment": "aligned",
                "modernization_priority": "maintain",
                "annual_cost": 250000.00,
            },
            {
                "unified_capability_name": "Customer Management",
                "application_name": "Microsoft Dynamics 365",
                "support_level": "full",
                "coverage_percentage": 92.0,
                "functional_fit_score": 92.0,
                "health_status": "healthy",
                "strategic_alignment": "aligned",
                "modernization_priority": "maintain",
                "annual_cost": 280000.00,
            },
            # Order Processing implementations
            {
                "unified_capability_name": "Order Processing",
                "application_name": "SAP S/4HANA",
                "support_level": "full",
                "coverage_percentage": 98.0,
                "functional_fit_score": 96.0,
                "health_status": "healthy",
                "strategic_alignment": "aligned",
                "modernization_priority": "maintain",
                "annual_cost": 500000.00,
            },
            {
                "unified_capability_name": "Order Processing",
                "application_name": "Oracle Fusion",
                "support_level": "full",
                "coverage_percentage": 95.0,
                "functional_fit_score": 94.0,
                "health_status": "healthy",
                "strategic_alignment": "aligned",
                "modernization_priority": "maintain",
                "annual_cost": 450000.00,
            },
            # Supply Chain Management implementations
            {
                "unified_capability_name": "Supply Chain Management",
                "application_name": "SAP S/4HANA",
                "support_level": "full",
                "coverage_percentage": 94.0,
                "functional_fit_score": 93.0,
                "health_status": "healthy",
                "strategic_alignment": "aligned",
                "modernization_priority": "maintain",
                "annual_cost": 500000.00,
            },
        ]

        created = 0
        skipped = 0

        for mapping_data in mappings:
            try:
                # Find unified capability
                unified_cap = UnifiedCapability.query.filter_by(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                    name=mapping_data["unified_capability_name"]
                ).first()

                if not unified_cap:
                    print(
                        f"  ⚠️  UnifiedCapability not found: {mapping_data['unified_capability_name']}"
                    )
                    skipped += 1
                    continue

                # Find application
                app = ApplicationComponent.query.filter_by(
                    name=mapping_data["application_name"]
                ).first()

                if not app:
                    print(
                        f"  ⚠️  ApplicationComponent not found: {mapping_data['application_name']}"
                    )
                    skipped += 1
                    continue

                # Check if mapping already exists
                existing = UnifiedCapabilityApplicationMapping.query.filter_by(
                    unified_capability_id=unified_cap.id, application_component_id=app.id
                ).first()

                if existing:
                    skipped += 1
                    continue

                # Create mapping
                mapping = UnifiedCapabilityApplicationMapping(
                    unified_capability_id=unified_cap.id,
                    application_component_id=app.id,
                    support_level=mapping_data.get("support_level"),
                    coverage_percentage=mapping_data.get("coverage_percentage", 75.0),
                    functional_fit_score=mapping_data.get("functional_fit_score", 75.0),
                    health_status=mapping_data.get("health_status"),
                    strategic_alignment=mapping_data.get("strategic_alignment"),
                    modernization_priority=mapping_data.get("modernization_priority"),
                    annual_cost=mapping_data.get("annual_cost"),
                )

                db.session.add(mapping)
                created += 1

            except Exception as e:
                print(f"  ❌ Error creating mapping: {e}")
                db.session.rollback()

        try:
            db.session.commit()
            print(
                f"✅ UnifiedCapability → ApplicationComponent mappings: {created} created, {skipped} skipped"
            )
        except Exception as e:
            print(f"❌ Error committing mappings: {e}")
            db.session.rollback()


class CapabilityVendorOrganizationMappingSeeder:
    """
    Seeds UnifiedCapability ↔ VendorOrganization (strategic) mappings.

    Example: Which vendors are strategic for "Order Processing"?
    - SAP (primary)
    - Oracle (secondary)
    """

    @staticmethod
    def seed():
        """Seed unified capability to vendor organization (strategic) mappings."""

        mappings = [
            # Order Processing strategic vendors
            {
                "unified_capability_name": "Order Processing",
                "vendor_org_name": "SAP",
                "relationship_type": "primary",
                "strategic_importance": "critical",
                "relationship_strength": 95.0,
                "capability_coverage_percentage": 98.0,
                "number_of_products": 3,
                "annual_spend": 500000.00,
                "multi_year_commitment": True,
                "dependency_level": "critical",
                "vendor_risk_level": "low",
                "market_position": "leader",
            },
            {
                "unified_capability_name": "Order Processing",
                "vendor_org_name": "Oracle",
                "relationship_type": "secondary",
                "strategic_importance": "high",
                "relationship_strength": 80.0,
                "capability_coverage_percentage": 95.0,
                "number_of_products": 2,
                "annual_spend": 300000.00,
                "multi_year_commitment": True,
                "dependency_level": "high",
                "vendor_risk_level": "low",
                "market_position": "leader",
            },
            # Customer Management strategic vendors
            {
                "unified_capability_name": "Customer Management",
                "vendor_org_name": "Salesforce",
                "relationship_type": "primary",
                "strategic_importance": "critical",
                "relationship_strength": 92.0,
                "capability_coverage_percentage": 95.0,
                "number_of_products": 4,
                "annual_spend": 250000.00,
                "multi_year_commitment": True,
                "dependency_level": "high",
                "vendor_risk_level": "low",
                "market_position": "leader",
            },
            {
                "unified_capability_name": "Customer Management",
                "vendor_org_name": "Microsoft",
                "relationship_type": "secondary",
                "strategic_importance": "high",
                "relationship_strength": 85.0,
                "capability_coverage_percentage": 92.0,
                "number_of_products": 3,
                "annual_spend": 150000.00,
                "multi_year_commitment": True,
                "dependency_level": "medium",
                "vendor_risk_level": "low",
                "market_position": "leader",
            },
        ]

        created = 0
        skipped = 0

        for mapping_data in mappings:
            try:
                # Find unified capability
                unified_cap = UnifiedCapability.query.filter_by(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                    name=mapping_data["unified_capability_name"]
                ).first()

                if not unified_cap:
                    print(
                        f"  ⚠️  UnifiedCapability not found: {mapping_data['unified_capability_name']}"
                    )
                    skipped += 1
                    continue

                # Find vendor organization
                vendor_org = VendorOrganization.query.filter_by(
                    vendor_name=mapping_data["vendor_org_name"]
                ).first()

                if not vendor_org:
                    print(f"  ⚠️  VendorOrganization not found: {mapping_data['vendor_org_name']}")
                    skipped += 1
                    continue

                # Check if mapping already exists
                existing = UnifiedCapabilityVendorOrganizationMapping.query.filter_by(
                    unified_capability_id=unified_cap.id, vendor_organization_id=vendor_org.id
                ).first()

                if existing:
                    skipped += 1
                    continue

                # Create mapping
                mapping = UnifiedCapabilityVendorOrganizationMapping(
                    unified_capability_id=unified_cap.id,
                    vendor_organization_id=vendor_org.id,
                    relationship_type=mapping_data.get("relationship_type"),
                    strategic_importance=mapping_data.get("strategic_importance"),
                    relationship_strength=mapping_data.get("relationship_strength", 70.0),
                    capability_coverage_percentage=mapping_data.get(
                        "capability_coverage_percentage", 70.0
                    ),
                    number_of_products=mapping_data.get("number_of_products", 0),
                    annual_spend=mapping_data.get("annual_spend"),
                    multi_year_commitment=mapping_data.get("multi_year_commitment", False),
                    dependency_level=mapping_data.get("dependency_level"),
                    vendor_risk_level=mapping_data.get("vendor_risk_level"),
                    market_position=mapping_data.get("market_position"),
                )

                db.session.add(mapping)
                created += 1

            except Exception as e:
                print(f"  ❌ Error creating mapping: {e}")
                db.session.rollback()

        try:
            db.session.commit()
            print(
                f"✅ UnifiedCapability → VendorOrganization mappings: {created} created, {skipped} skipped"
            )
        except Exception as e:
            print(f"❌ Error committing mappings: {e}")
            db.session.rollback()


class ApplicationVendorProductMappingSeeder:
    """
    Seeds ApplicationComponent ↔ VendorProduct (technology stack) mappings.

    Example: What vendor products does "Order Processing App" use?
    - SAP S/4HANA (core)
    - MuleSoft Anypoint (integration)
    - Tableau (analytics)
    """

    @staticmethod
    def seed():
        """Seed application to vendor product mappings (tech stack)."""

        mappings = [
            # SAP S/4HANA implementations
            {
                "application_name": "SAP Order Processing",
                "vendor_product_name": "SAP S/4HANA",
                "role_type": "core",
                "criticality": "mission_critical",
                "primary_product": True,
                "product_version": "2023",
                "deployment_model": "cloud",
                "number_of_users": 500,
                "license_cost_annual": 400000.00,
                "maintenance_cost_annual": 80000.00,
                "integration_level": "tightly_coupled",
                "uptime_percentage": 99.9,
            },
            # Salesforce implementation
            {
                "application_name": "Salesforce CRM",
                "vendor_product_name": "Salesforce Platform",
                "role_type": "core",
                "criticality": "mission_critical",
                "primary_product": True,
                "product_version": "2024",
                "deployment_model": "saas",
                "number_of_users": 1000,
                "license_cost_annual": 150000.00,
                "maintenance_cost_annual": 0.00,
                "integration_level": "loosely_coupled",
                "api_usage": True,
                "uptime_percentage": 99.99,
            },
            # Integration platform
            {
                "application_name": "SAP Order Processing",
                "vendor_product_name": "MuleSoft Anypoint",
                "role_type": "supporting",
                "criticality": "important",
                "primary_product": False,
                "product_version": "4.4",
                "deployment_model": "cloud",
                "license_cost_annual": 50000.00,
                "maintenance_cost_annual": 10000.00,
                "integration_level": "loosely_coupled",
                "api_usage": True,
                "number_of_interfaces": 25,
            },
            # Analytics platform
            {
                "application_name": "Analytics Dashboard",
                "vendor_product_name": "Tableau",
                "role_type": "supporting",
                "criticality": "important",
                "primary_product": True,
                "product_version": "2024.1",
                "deployment_model": "cloud",
                "number_of_users": 200,
                "license_cost_annual": 100000.00,
                "maintenance_cost_annual": 20000.00,
                "integration_level": "loosely_coupled",
                "api_usage": True,
            },
        ]

        created = 0
        skipped = 0

        for mapping_data in mappings:
            try:
                # Find application
                app = ApplicationComponent.query.filter_by(
                    name=mapping_data["application_name"]
                ).first()

                if not app:
                    print(
                        f"  ⚠️  ApplicationComponent not found: {mapping_data['application_name']}"
                    )
                    skipped += 1
                    continue

                # Find vendor product
                vendor_prod = VendorProduct.query.filter_by(
                    name=mapping_data["vendor_product_name"]
                ).first()

                if not vendor_prod:
                    print(f"  ⚠️  VendorProduct not found: {mapping_data['vendor_product_name']}")
                    skipped += 1
                    continue

                # Check if mapping already exists
                existing = ApplicationVendorProductMapping.query.filter_by(
                    application_component_id=app.id, vendor_product_id=vendor_prod.id
                ).first()

                if existing:
                    skipped += 1
                    continue

                # Create mapping
                mapping = ApplicationVendorProductMapping(
                    application_component_id=app.id,
                    vendor_product_id=vendor_prod.id,
                    role_type=mapping_data.get("role_type"),
                    criticality=mapping_data.get("criticality"),
                    primary_product=mapping_data.get("primary_product", False),
                    product_version=mapping_data.get("product_version"),
                    deployment_model=mapping_data.get("deployment_model"),
                    number_of_users=mapping_data.get("number_of_users"),
                    license_cost_annual=mapping_data.get("license_cost_annual"),
                    maintenance_cost_annual=mapping_data.get("maintenance_cost_annual"),
                    total_cost_annual=mapping_data.get("license_cost_annual", 0)
                    + mapping_data.get("maintenance_cost_annual", 0),
                    integration_level=mapping_data.get("integration_level"),
                    api_usage=mapping_data.get("api_usage", False),
                    number_of_interfaces=mapping_data.get("number_of_interfaces", 0),
                    uptime_percentage=mapping_data.get("uptime_percentage"),
                )

                db.session.add(mapping)
                created += 1

            except Exception as e:
                print(f"  ❌ Error creating mapping: {e}")
                db.session.rollback()

        try:
            db.session.commit()
            print(
                f"✅ ApplicationComponent → VendorProduct mappings: {created} created, {skipped} skipped"
            )
        except Exception as e:
            print(f"❌ Error committing mappings: {e}")
            db.session.rollback()


def seed_all_capability_vendor_app_mappings():
    """Run all mapping seeders."""
    print("\n🌱 Seeding Capability-to-Vendor/Application Mappings...")
    print("=" * 60)

    CapabilityVendorMappingSeeder.seed()
    CapabilityApplicationMappingSeeder.seed()
    CapabilityVendorOrganizationMappingSeeder.seed()
    ApplicationVendorProductMappingSeeder.seed()

    print("=" * 60)
    print("✅ All mapping seeders completed!")
