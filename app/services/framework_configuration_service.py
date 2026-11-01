"""
Framework Configuration Service

Service layer for managing framework configurations and extensions.
Provides configuration-driven capability management with industry extensions.

Services:
- Framework configuration management
- Extension loading and application
- Capability generation based on configuration
- Migration support from legacy frameworks
- Validation and compliance checking
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional


from app import db
from app.models.framework_configuration import (
    CapabilityFrameworkConfiguration,
    FrameworkExtension,
    FrameworkInstance,
    FrameworkMigrationMapping,
    FrameworkValidationRule,
)
from app.models.unified_capability import (
    BusinessDomain,
    UnifiedCapability,
)


class FrameworkConfigurationService:
    """Service for managing framework configurations"""

    @staticmethod
    def create_configuration(config_data: Dict) -> CapabilityFrameworkConfiguration:
        """Create a new framework configuration"""
        configuration = CapabilityFrameworkConfiguration(
            configuration_name=config_data["configuration_name"],
            configuration_description=config_data.get("configuration_description"),
            configuration_code=config_data["configuration_code"],
            organization_name=config_data.get("organization_name"),
            organization_type=config_data.get("organization_type"),
            industry_focus=config_data.get("industry_focus", "Manufacturing"),
            capability_levels=config_data.get("capability_levels", 3),
            manufacturing_model=config_data.get("manufacturing_model"),
            manufacturing_complexity=config_data.get("manufacturing_complexity"),
            configuration_owner=config_data.get("configuration_owner"),
            technical_owner=config_data.get("technical_owner"),
        )

        # Set enabled domains
        if "enabled_domains" in config_data:
            configuration.set_enabled_domains(config_data["enabled_domains"])

        # Set enabled extensions
        if "enabled_extensions" in config_data:
            configuration.set_enabled_extensions(config_data["enabled_extensions"])

        db.session.add(configuration)
        db.session.commit()

        # Generate capabilities based on configuration
        FrameworkConfigurationService._generate_capabilities_for_configuration(configuration)

        return configuration

    @staticmethod
    def get_configuration_by_id(config_id: int) -> Optional[CapabilityFrameworkConfiguration]:
        """Get configuration by ID"""
        return CapabilityFrameworkConfiguration.query.get(config_id)

    @staticmethod
    def get_active_configuration(
        organization_name: str = None,
    ) -> Optional[CapabilityFrameworkConfiguration]:
        """Get active configuration for organization"""
        query = CapabilityFrameworkConfiguration.query.filter_by(status="active")
        if organization_name:
            query = query.filter_by(organization_name=organization_name)
        else:
            query = query.filter_by(organization_name=None)

        return query.order_by(CapabilityFrameworkConfiguration.created_at.desc()).first()

    @staticmethod
    def update_configuration(
        config_id: int, update_data: Dict
    ) -> Optional[CapabilityFrameworkConfiguration]:
        """Update framework configuration"""
        configuration = CapabilityFrameworkConfiguration.query.get(config_id)
        if not configuration:
            return None

        # Update basic fields
        for field, value in update_data.items():
            if hasattr(configuration, field) and field not in ["id", "created_at"]:
                setattr(configuration, field, value)

        # Handle JSON fields
        if "enabled_domains" in update_data:
            configuration.set_enabled_domains(update_data["enabled_domains"])

        if "enabled_extensions" in update_data:
            configuration.set_enabled_extensions(update_data["enabled_extensions"])

        configuration.updated_at = datetime.utcnow()
        db.session.commit()

        # Regenerate capabilities if configuration changed significantly
        if FrameworkConfigurationService._requires_capability_regeneration(update_data):
            FrameworkConfigurationService._regenerate_capabilities_for_configuration(configuration)

        return configuration

    @staticmethod
    def delete_configuration(config_id: int) -> bool:
        """Delete framework configuration"""
        configuration = CapabilityFrameworkConfiguration.query.get(config_id)
        if not configuration:
            return False

        # Check if configuration is in use
        instances = FrameworkInstance.query.filter_by(configuration_id=config_id).count()
        if instances > 0:
            raise ValueError(f"Cannot delete configuration with {instances} active instances")

        db.session.delete(configuration)
        db.session.commit()
        return True

    @staticmethod
    def _generate_capabilities_for_configuration(configuration: CapabilityFrameworkConfiguration):
        """Generate capabilities based on configuration"""
        enabled_domains = configuration.get_enabled_domains()
        enabled_extensions = configuration.get_enabled_extensions()

        # Generate base capabilities for enabled domains
        for domain_code in enabled_domains:
            domain = BusinessDomain.query.filter_by(code=domain_code).first()
            if domain:
                FrameworkConfigurationService._generate_domain_capabilities(domain, configuration)

        # Apply extensions
        for extension_code in enabled_extensions:
            FrameworkConfigurationService._apply_extension(extension_code, configuration)

    @staticmethod
    def _generate_domain_capabilities(
        domain: BusinessDomain, configuration: CapabilityFrameworkConfiguration
    ):
        """Generate capabilities for a specific domain"""
        # Get base capability templates for domain
        base_capabilities = FrameworkConfigurationService._get_base_capabilities_for_domain(
            domain.code
        )

        for cap_data in base_capabilities:
            # Check if capability already exists
            existing = UnifiedCapability.query.filter_by(code=cap_data["code"]).first()  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
            if not existing:
                capability = UnifiedCapability(
                    name=cap_data["name"],
                    code=cap_data["code"],
                    description=cap_data["description"],
                    level=cap_data["level"],
                    domain_id=domain.id,
                    category=cap_data["category"],
                    capability_type=cap_data["capability_type"],
                    strategic_importance=cap_data["strategic_importance"],
                    business_criticality=cap_data["business_criticality"],
                    is_core_differentiator=cap_data.get("is_core_differentiator", False),
                )

                # Apply configuration customizations
                FrameworkConfigurationService._apply_capability_customizations(
                    capability, configuration
                )

                db.session.add(capability)

        db.session.commit()

    @staticmethod
    def _get_base_capabilities_for_domain(domain_code: str) -> List[Dict]:
        """Get base capabilities for a domain"""
        capability_templates = {
            "CUST": [
                {
                    "name": "Customer Relationship Management",
                    "code": "CUST-CRM",
                    "level": 1,
                    "category": "core",
                    "capability_type": "strategic",
                    "description": "Manage customer relationships across the entire lifecycle",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": True,
                },
                {
                    "name": "Customer Experience Management",
                    "code": "CUST-CEM",
                    "level": 1,
                    "category": "differentiating",
                    "capability_type": "strategic",
                    "description": "Design and manage end-to-end customer experiences",
                    "strategic_importance": "high",
                    "business_criticality": "important",
                    "is_core_differentiator": True,
                },
            ],
            "PROD": [
                {
                    "name": "Product Strategy & Portfolio Management",
                    "code": "PROD-PSPM",
                    "level": 1,
                    "category": "core",
                    "capability_type": "strategic",
                    "description": "Define product strategy and manage product portfolio",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": True,
                },
                {
                    "name": "Product Development & Innovation",
                    "code": "PROD-PDI",
                    "level": 1,
                    "category": "differentiating",
                    "capability_type": "strategic",
                    "description": "Develop innovative products and solutions",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": True,
                },
            ],
            "OPER": [
                {
                    "name": "Production Management",
                    "code": "OPER-PROD",
                    "level": 1,
                    "category": "core",
                    "capability_type": "strategic",
                    "description": "Manage manufacturing production processes",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": True,
                    "manufacturing_critical": True,
                    "industry_domain": "Manufacturing",
                },
                {
                    "name": "Supply Chain Management",
                    "code": "OPER-SCM",
                    "level": 1,
                    "category": "core",
                    "capability_type": "strategic",
                    "description": "Manage end-to-end supply chain operations",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": True,
                    "manufacturing_critical": True,
                    "industry_domain": "Manufacturing",
                },
                {
                    "name": "Quality Management",
                    "code": "OPER-QM",
                    "level": 1,
                    "category": "core",
                    "capability_type": "strategic",
                    "description": "Ensure product and service quality",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": True,
                    "manufacturing_critical": True,
                    "industry_domain": "Manufacturing",
                },
            ],
            "FIN": [
                {
                    "name": "Financial Planning & Analysis",
                    "code": "FIN-FP&A",
                    "level": 1,
                    "category": "core",
                    "capability_type": "strategic",
                    "description": "Plan, budget, and analyze financial performance",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": False,
                }
            ],
            "RISK": [
                {
                    "name": "Enterprise Risk Management",
                    "code": "RISK-ERM",
                    "level": 1,
                    "category": "supporting",
                    "capability_type": "strategic",
                    "description": "Identify, assess, and mitigate enterprise risks",
                    "strategic_importance": "high",
                    "business_criticality": "important",
                    "is_core_differentiator": False,
                }
            ],
            "DATA": [
                {
                    "name": "Data Management & Governance",
                    "code": "DATA-DMG",
                    "level": 1,
                    "category": "enabling",
                    "capability_type": "strategic",
                    "description": "Manage data assets and ensure data quality",
                    "strategic_importance": "high",
                    "business_criticality": "important",
                    "is_core_differentiator": False,
                },
                {
                    "name": "Business Intelligence & Analytics",
                    "code": "DATA-BIA",
                    "level": 1,
                    "category": "enabling",
                    "capability_type": "strategic",
                    "description": "Provide business insights through analytics",
                    "strategic_importance": "high",
                    "business_criticality": "important",
                    "is_core_differentiator": True,
                },
            ],
            "PART": [
                {
                    "name": "Supplier Relationship Management",
                    "code": "PART-SRM",
                    "level": 1,
                    "category": "supporting",
                    "capability_type": "strategic",
                    "description": "Manage supplier relationships and performance",
                    "strategic_importance": "high",
                    "business_criticality": "important",
                    "is_core_differentiator": False,
                    "manufacturing_critical": True,
                    "industry_domain": "Manufacturing",
                }
            ],
            "WORK": [
                {
                    "name": "Talent Management",
                    "code": "WORK-TM",
                    "level": 1,
                    "category": "supporting",
                    "capability_type": "strategic",
                    "description": "Attract, develop, and retain talent",
                    "strategic_importance": "high",
                    "business_criticality": "important",
                    "is_core_differentiator": True,
                }
            ],
            "TECH": [
                {
                    "name": "Digital Platform Management",
                    "code": "TECH-DPM",
                    "level": 1,
                    "category": "enabling",
                    "capability_type": "strategic",
                    "description": "Manage digital platforms and infrastructure",
                    "strategic_importance": "critical",
                    "business_criticality": "mission_critical",
                    "is_core_differentiator": False,
                },
                {
                    "name": "Technology Innovation",
                    "code": "TECH-TI",
                    "level": 1,
                    "category": "differentiating",
                    "capability_type": "strategic",
                    "description": "Drive technology innovation and digital transformation",
                    "strategic_importance": "high",
                    "business_criticality": "important",
                    "is_core_differentiator": True,
                },
            ],
        }

        return capability_templates.get(domain_code, [])

    @staticmethod
    def _apply_capability_customizations(
        capability: UnifiedCapability, configuration: CapabilityFrameworkConfiguration
    ):
        """Apply configuration customizations to capability"""
        # Apply industry-specific customizations
        if configuration.industry_focus == "Manufacturing":
            if capability.domain and capability.domain.code in ["OPER", "PART"]:
                capability.manufacturing_critical = True
                capability.industry_domain = "Manufacturing"

        # Apply manufacturing model customizations
        if configuration.manufacturing_model:
            # Add manufacturing model-specific attributes
            pass

        # Apply complexity customizations
        if configuration.manufacturing_complexity:
            # Adjust capability based on complexity
            pass

    @staticmethod
    def _apply_extension(extension_code: str, configuration: CapabilityFrameworkConfiguration):
        """Apply extension to configuration"""
        extension = FrameworkExtension.query.filter_by(extension_code=extension_code).first()
        if not extension:
            return

        # Apply extension capabilities
        if extension.additional_capabilities:
            capabilities_data = json.loads(extension.additional_capabilities)
            for cap_data in capabilities_data:
                existing = UnifiedCapability.query.filter_by(code=cap_data["code"]).first()  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                if not existing:
                    capability = UnifiedCapability(
                        name=cap_data["name"],
                        code=cap_data["code"],
                        description=cap_data["description"],
                        level=cap_data["level"],
                        category=cap_data["category"],
                        capability_type=cap_data["capability_type"],
                    )
                    db.session.add(capability)

        db.session.commit()

    @staticmethod
    def _requires_capability_regeneration(update_data: Dict) -> bool:
        """Check if configuration changes require capability regeneration"""
        regeneration_triggers = [
            "enabled_domains",
            "enabled_extensions",
            "capability_levels",
            "industry_focus",
        ]

        return any(trigger in update_data for trigger in regeneration_triggers)

    @staticmethod
    def _regenerate_capabilities_for_configuration(configuration: CapabilityFrameworkConfiguration):
        """Regenerate capabilities for configuration"""
        # Delete existing capabilities for this configuration
        # This is a simplified approach - in production, you'd want more sophisticated handling
        FrameworkConfigurationService._generate_capabilities_for_configuration(configuration)


class FrameworkExtensionService:
    """Service for managing framework extensions"""

    @staticmethod
    def get_available_extensions(industry: str = None) -> List[FrameworkExtension]:
        """Get available extensions"""
        query = FrameworkExtension.query.filter_by(status="active")
        if industry:
            query = query.filter(FrameworkExtension.extension_category == industry)

        return query.all()

    @staticmethod
    def get_extension_by_code(extension_code: str) -> Optional[FrameworkExtension]:
        """Get extension by code"""
        return FrameworkExtension.query.filter_by(extension_code=extension_code).first()

    @staticmethod
    def install_extension(configuration_id: int, extension_code: str) -> bool:
        """Install extension on configuration"""
        configuration = CapabilityFrameworkConfiguration.query.get(configuration_id)
        extension = FrameworkExtension.query.filter_by(extension_code=extension_code).first()

        if not configuration or not extension:
            return False

        # Check compatibility
        if not FrameworkExtensionService._is_extension_compatible(configuration, extension):
            return False

        # Add extension to enabled extensions
        enabled_extensions = configuration.get_enabled_extensions()
        if extension_code not in enabled_extensions:
            enabled_extensions.append(extension_code)
            configuration.set_enabled_extensions(enabled_extensions)

            # Apply extension
            FrameworkConfigurationService._apply_extension(extension_code, configuration)

            db.session.commit()

        return True

    @staticmethod
    def _is_extension_compatible(
        configuration: CapabilityFrameworkConfiguration, extension: FrameworkExtension
    ) -> bool:
        """Check if extension is compatible with configuration"""
        # Check framework compatibility
        if extension.target_framework != configuration.base_framework:
            return False

        # Check version compatibility
        if extension.compatible_versions:
            compatible_versions = json.loads(extension.compatible_versions)
            if configuration.framework_version not in compatible_versions:
                return False

        # Check industry compatibility
        if (
            extension.extension_category
            and extension.extension_category != configuration.industry_focus
        ):
            return False

        return True


class FrameworkValidationService:
    """Service for validating framework configurations"""

    @staticmethod
    def validate_configuration(configuration_id: int) -> Dict[str, Any]:
        """Validate framework configuration"""
        configuration = CapabilityFrameworkConfiguration.query.get(configuration_id)
        if not configuration:
            return {"valid": False, "errors": ["Configuration not found"]}

        validation_rules = FrameworkValidationRule.query.filter_by(status="active").all()

        errors = []
        warnings = []
        recommendations = []

        for rule in validation_rules:
            result = FrameworkValidationService._apply_validation_rule(configuration, rule)
            if result["status"] == "error":
                errors.extend(result["messages"])
            elif result["status"] == "warning":
                warnings.extend(result["messages"])
            elif result["status"] == "recommendation":
                recommendations.extend(result["messages"])

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "recommendations": recommendations,
            "validation_timestamp": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def _apply_validation_rule(
        configuration: CapabilityFrameworkConfiguration, rule: FrameworkValidationRule
    ) -> Dict[str, Any]:
        """Apply a single validation rule"""
        # This is a simplified validation - in production, you'd implement more sophisticated rule processing
        messages = []
        status = "passed"

        # Example validation rules
        if rule.rule_code == "DOMAIN_COMPLETENESS":
            enabled_domains = configuration.get_enabled_domains()
            if len(enabled_domains) < 3:
                messages.append("Configuration should include at least 3 business domains")
                status = "warning"

        elif rule.rule_code == "MANUFACTURING_REQUIREMENTS":
            if configuration.industry_focus == "Manufacturing":
                enabled_domains = configuration.get_enabled_domains()
                if "OPER" not in enabled_domains:
                    messages.append("Manufacturing configuration should include Operations domain")
                    status = "error"

        return {"status": status, "messages": messages}


class FrameworkMigrationService:
    """Service for migrating from legacy frameworks"""

    @staticmethod
    def create_migration_mapping(migration_data: Dict) -> FrameworkMigrationMapping:
        """Create migration mapping"""
        migration = FrameworkMigrationMapping(
            migration_name=migration_data["migration_name"],
            migration_description=migration_data.get("migration_description"),
            migration_code=migration_data["migration_code"],
            source_framework_name=migration_data["source_framework_name"],
            source_framework_version=migration_data.get("source_framework_version"),
            source_framework_type=migration_data.get("source_framework_type", "custom"),
            target_configuration_id=migration_data["target_configuration_id"],
        )

        # Set mapping rules
        if "domain_mappings" in migration_data:
            migration.domain_mappings = json.dumps(migration_data["domain_mappings"])

        if "capability_mappings" in migration_data:
            migration.capability_mappings = json.dumps(migration_data["capability_mappings"])

        db.session.add(migration)
        db.session.commit()

        return migration

    @staticmethod
    def execute_migration(migration_id: int) -> Dict[str, Any]:
        """Execute framework migration"""
        migration = FrameworkMigrationMapping.query.get(migration_id)
        if not migration:
            return {"success": False, "error": "Migration not found"}

        try:
            # Update migration status
            migration.status = "in_progress"
            migration.actual_start_date = datetime.utcnow().date()
            db.session.commit()

            # Execute migration phases
            migration_results = FrameworkMigrationService._execute_migration_phases(migration)

            # Update migration completion
            migration.status = "completed"
            migration.actual_end_date = datetime.utcnow().date()
            migration.migration_percentage = 100.0
            db.session.commit()

            return {
                "success": True,
                "results": migration_results,
                "completion_date": migration.actual_end_date.isoformat(),
            }

        except Exception as e:
            migration.status = "failed"
            db.session.commit()
            return {"success": False, "error": str(e)}

    @staticmethod
    def _execute_migration_phases(migration: FrameworkMigrationMapping) -> Dict[str, Any]:
        """Execute migration phases"""
        # This is a simplified migration execution
        # In production, you'd implement sophisticated migration logic

        results = {
            "domains_migrated": 0,
            "capabilities_migrated": 0,
            "relationships_migrated": 0,
            "errors": [],
        }

        # Parse mapping rules
        if migration.domain_mappings:
            domain_mappings = json.loads(migration.domain_mappings)
            results["domains_migrated"] = len(domain_mappings)

        if migration.capability_mappings:
            capability_mappings = json.loads(migration.capability_mappings)
            results["capabilities_migrated"] = len(capability_mappings)

        return results
