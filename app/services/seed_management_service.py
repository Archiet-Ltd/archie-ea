"""
Seed Management Service

Provides a unified interface for checking seed data status and triggering
seeding operations from the admin UI. Wraps the UnifiedSeedOrchestrator
and standalone seeders with status detection and dispatch logic.
"""

import logging

from app import db

logger = logging.getLogger(__name__)

# Registry of all seed categories with metadata
SEED_CATEGORIES = [
    {
        "key": "vendor_organizations",
        "name": "Vendor Organizations",
        "description": "Enterprise vendor organizations (SAP, Oracle, Microsoft, etc.)",
        "icon": "building-2",
        "model_path": "app.models.vendor.vendor_organization.VendorOrganization",
        "seeder_type": "orchestrator",
        "dependencies": [],
    },
    {
        "key": "business_capabilities",
        "name": "Business Capabilities",
        "description": "Business capability taxonomy (L1-L3 hierarchy with APQC mappings)",
        "icon": "target",
        "model_path": "app.models.business_capabilities.BusinessCapability",
        "seeder_type": "orchestrator",
        "dependencies": [],
    },
    {
        "key": "technical_capabilities",
        "name": "Technical Capabilities",
        "description": "Technical capability domains and technology stacks",
        "icon": "cpu",
        "model_path": "app.models.technical_capability.TechnicalCapability",
        "seeder_type": "orchestrator",
        "dependencies": [],
    },
    {
        "key": "vendor_products",
        "name": "Vendor Products",
        "description": "Individual vendor products and solutions catalog",
        "icon": "package",
        "model_path": "app.models.vendor.vendor_organization.VendorProduct",
        "seeder_type": "orchestrator",
        "dependencies": ["vendor_organizations"],
    },
    {
        "key": "feature_flags",
        "name": "Feature Flags",
        "description": "Default feature flags for sidebar sections and UI controls",
        "icon": "toggle-left",
        "model_path": "app.models.feature_flags.FeatureFlag",
        "seeder_type": "standalone",
        "dependencies": [],
    },
    {
        "key": "apqc_processes",
        "name": "APQC Processes",
        "description": "APQC Process Classification Framework hierarchy",
        "icon": "workflow",
        "model_path": "app.models.apqc_process.APQCProcess",
        "seeder_type": "standalone",
        "dependencies": [],
    },
    {
        "key": "manufacturing_domains",
        "name": "Manufacturing Domains",
        "description": "Manufacturing domain hierarchy (Production, Supply Chain, etc.)",
        "icon": "factory",
        "model_path": "app.models.manufacturing_capability.ManufacturingDomainHierarchy",
        "seeder_type": "standalone",
        "dependencies": [],
    },
    {
        "key": "capability_vendor_mappings",
        "name": "Capability-Vendor Mappings",
        "description": "Junction tables linking capabilities to vendors and products",
        "icon": "link",
        "model_path": "app.models.capability_to_vendor_mapping.TechnicalCapabilityVendorMapping",
        "seeder_type": "standalone",
        "dependencies": ["technical_capabilities", "vendor_products"],
    },
    {
        "key": "ai_prompt_templates",
        "name": "AI Prompt Templates",
        "description": "Default AI prompt templates for architecture generation",
        "icon": "message-square",
        "model_path": "app.models.ai_service.AIPromptTemplate",
        "seeder_type": "standalone",
        "dependencies": [],
    },
    {
        "key": "business_domains",
        "name": "Business Domains",
        "description": "Enterprise business domains (Finance, HR, IT, Operations, etc.)",
        "icon": "briefcase",
        "model_path": "app.models.unified_capability.BusinessDomain",
        "seeder_type": "standalone",
        "dependencies": [],
    },
    {
        "key": "vendor_json_domains",
        "name": "Vendor JSON Domains",
        "description": "Import vendors from domain JSON files (vendor json/ directory) - L1/L2/L3 tiers",
        "icon": "folder-open",
        "model_path": "app.models.vendor.vendor_organization.VendorOrganization",
        "seeder_type": "standalone",
        "dependencies": ["business_capabilities", "technical_capabilities"],
    },
]


def _import_model(model_path):
    """Dynamically import a model class from a dotted path."""
    module_path, class_name = model_path.rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


class SeedManagementService:
    """Centralized seed management for the admin UI."""

    def get_seed_status(self):
        """Alias for get_all_status() for route compatibility."""
        return self.get_all_status()

    def get_all_status(self):
        """Get status of all seed categories with record counts."""
        results = []
        for cat in SEED_CATEGORIES:
            try:
                model_class = _import_model(cat["model_path"])
                record_count = model_class.query.count()  # model-safety-ok: each iteration queries a different model/table
                status = "seeded" if record_count > 0 else "empty"
            except Exception as e:
                logger.warning(f"Error checking seed status for {cat['key']}: {e}")
                record_count = 0
                status = "error"

            results.append({
                "key": cat["key"],
                "name": cat["name"],
                "description": cat["description"],
                "icon": cat["icon"],
                "record_count": record_count,
                "status": status,
                "dependencies": cat["dependencies"],
                "seeder_type": cat["seeder_type"],
            })
        return results

    def seed_category(self, key):
        """Trigger seeding for a specific category. Returns result dict."""
        cat = next((c for c in SEED_CATEGORIES if c["key"] == key), None)
        if not cat:
            return {"success": False, "message": f"Unknown seed category: {key}"}

        try:
            if cat["seeder_type"] == "orchestrator":
                return self._seed_via_orchestrator(key)
            else:
                return self._seed_standalone(key)
        except Exception as e:
            logger.error(f"Error seeding {key}: {e}")
            return {"success": False, "message": f"Error seeding {key}: {str(e)}"}

    def seed_all(self):
        """Run all seeders: orchestrator first, then standalone."""
        results = {}

        # Orchestrator seeders first (handles dependency order)
        try:
            from app.services.unified_seed_orchestrator import UnifiedSeedOrchestrator
            orchestrator = UnifiedSeedOrchestrator()
            orch_result = orchestrator.seed_all(skip_errors=True)
            results["orchestrator"] = orch_result
        except Exception as e:
            logger.error(f"Orchestrator seeding error: {e}")
            results["orchestrator"] = {"success": False, "message": str(e)}

        # Standalone seeders
        standalone_keys = [c["key"] for c in SEED_CATEGORIES if c["seeder_type"] == "standalone"]
        for key in standalone_keys:
            try:
                result = self._seed_standalone(key)
                results[key] = result
            except Exception as e:
                logger.error(f"Error seeding {key}: {e}")
                results[key] = {"success": False, "message": str(e)}

        all_success = all(r.get("success", False) for r in results.values())
        return {
            "success": all_success,
            "message": "All seeders completed" if all_success else "Some seeders had errors",
            "results": results,
        }

    def _seed_via_orchestrator(self, key):
        """Seed a category managed by UnifiedSeedOrchestrator."""
        from app.services.unified_seed_orchestrator import UnifiedSeedOrchestrator
        orchestrator = UnifiedSeedOrchestrator()
        result = orchestrator.seed_specific([key])
        return result

    def _seed_standalone(self, key):
        """Seed a standalone category not managed by the orchestrator."""
        if key == "feature_flags":
            return self._seed_feature_flags()
        elif key == "apqc_processes":
            return self._seed_apqc_processes()
        elif key == "manufacturing_domains":
            return self._seed_manufacturing_domains()
        elif key == "capability_vendor_mappings":
            return self._seed_capability_vendor_mappings()
        elif key == "ai_prompt_templates":
            return self._seed_ai_prompt_templates()
        elif key == "business_domains":
            return self._seed_business_domains()
        elif key == "vendor_json_domains":
            return self._seed_vendor_json_domains()
        else:
            return {"success": False, "message": f"No standalone seeder for: {key}"}

    def _seed_feature_flags(self):
        """Seed default feature flags (same logic as flask seed-feature-flags)."""
        from sqlalchemy import text
        from app.models import FeatureFlag, FeatureState, FeatureType

        flag_defs = [
            {"key": "applications_management", "name": "Applications Management",
             "description": "Application portfolio and rationalization features",
             "feature_type": FeatureType.SIDEBAR_SECTION.value,
             "state": FeatureState.STABLE.value, "enabled": True,
             "sidebar_label": "Applications Management", "sidebar_icon": "package",
             "routes": ["/applications/*"], "sort_order": 10},
            {"key": "vendor_management", "name": "Vendor Management",
             "description": "Vendor portfolio and analysis features",
             "feature_type": FeatureType.SIDEBAR_SECTION.value,
             "state": FeatureState.STABLE.value, "enabled": True,
             "sidebar_label": "Vendor Management", "sidebar_icon": "building-2",
             "routes": ["/vendors/*"], "sort_order": 20},
            {"key": "section_administration", "name": "Administration",
             "description": "System administration and configuration",
             "feature_type": FeatureType.SIDEBAR_SECTION.value,
             "state": FeatureState.STABLE.value, "enabled": True,
             "sidebar_label": "Administration", "sidebar_icon": "settings",
             "routes": ["/admin/*"], "sort_order": 100},
            {"key": "admin_users", "name": "Admin Users",
             "description": "User management in admin panel",
             "feature_type": FeatureType.SIDEBAR_SECTION.value,
             "state": FeatureState.STABLE.value, "enabled": True,
             "sidebar_label": "Users", "sidebar_icon": "users",
             "routes": ["/admin/users*"], "sort_order": 101},
            {"key": "admin_api_settings", "name": "Admin API Settings",
             "description": "API key management in admin panel",
             "feature_type": FeatureType.SIDEBAR_SECTION.value,
             "state": FeatureState.STABLE.value, "enabled": True,
             "sidebar_label": "AI settings", "sidebar_icon": "key",
             "routes": ["/admin/api-settings*"], "sort_order": 102},
        ]

        created = 0
        skipped = 0
        for flag_data in flag_defs:
            existing = db.session.execute(  # tenant-exempt: CLI command
                text("SELECT 1 FROM feature_flags WHERE key = :key"),
                {"key": flag_data["key"]},
            ).scalar()
            if existing:
                skipped += 1
                continue
            flag = FeatureFlag(**flag_data)
            db.session.add(flag)
            created += 1

        db.session.commit()
        return {"success": True, "message": f"Created {created}, skipped {skipped}",
                "data": {"created": created, "skipped": skipped}}

    def _seed_apqc_processes(self):
        """Seed APQC processes."""
        from app.services.seed_apqc_vendor_mapping import APQCVendorSeedingService
        service = APQCVendorSeedingService()
        result = service.seed_apqc_processes()
        return {"success": True, "message": "APQC processes seeded", "data": result or {}}

    def _seed_manufacturing_domains(self):
        """Seed manufacturing domain hierarchy."""
        from app.services.manufacturing_domain_hierarchy_seeder import ManufacturingDomainHierarchySeeder
        seeder = ManufacturingDomainHierarchySeeder()
        result = seeder.seed()
        return result if isinstance(result, dict) else {"success": True, "message": "Manufacturing domains seeded"}

    def _seed_capability_vendor_mappings(self):
        """Seed capability-vendor junction tables from domain JSON files."""
        # Step 1: Import vendors/products/mappings from domain JSON files
        from app.services.vendor_json_domain_importer import VendorJsonDomainImporter
        importer = VendorJsonDomainImporter()
        json_result = importer.run(commit=True)

        # Step 2: Run existing hardcoded mapping seeder as supplement
        from app.services.capability_vendor_app_mapping_seeder import seed_all_capability_vendor_app_mappings
        seed_all_capability_vendor_app_mappings()

        return json_result

    def _seed_ai_prompt_templates(self):
        """Seed default AI prompt templates."""
        from app.services.ai_prompt_seeder import seed_default_ai_prompt_templates
        seed_default_ai_prompt_templates()
        return {"success": True, "message": "AI prompt templates seeded"}

    def _seed_business_domains(self):
        """Seed standard enterprise business domains for vendor analysis."""
        from sqlalchemy import text
        from app.models.unified_capability import BusinessDomain

        domain_defs = [
            {"code": "FIN", "name": "Finance & Accounting",
             "description": "Financial planning, accounting, treasury, and reporting",
             "domain_type": "supporting", "strategic_focus": "Financial excellence"},
            {"code": "HR", "name": "Human Resources",
             "description": "Talent management, payroll, benefits, and workforce planning",
             "domain_type": "supporting", "strategic_focus": "People and culture"},
            {"code": "OPS", "name": "Operations",
             "description": "Business operations, process management, and quality assurance",
             "domain_type": "primary", "strategic_focus": "Operational excellence"},
            {"code": "IT", "name": "Information Technology",
             "description": "IT infrastructure, applications, security, and digital transformation",
             "domain_type": "enabling", "strategic_focus": "Technology enablement"},
            {"code": "SALES", "name": "Sales & Marketing",
             "description": "Sales, marketing, CRM, and customer acquisition",
             "domain_type": "primary", "strategic_focus": "Revenue growth"},
            {"code": "SCM", "name": "Supply Chain & Logistics",
             "description": "Procurement, logistics, warehousing, and supplier management",
             "domain_type": "primary", "strategic_focus": "Supply chain optimization"},
            {"code": "MFG", "name": "Manufacturing",
             "description": "Production planning, MES, quality control, and plant operations",
             "domain_type": "primary", "strategic_focus": "Manufacturing excellence"},
            {"code": "CUST", "name": "Customer Service",
             "description": "Customer support, service desk, and customer experience management",
             "domain_type": "primary", "strategic_focus": "Customer experience"},
            {"code": "LEGAL", "name": "Legal & Compliance",
             "description": "Legal, regulatory compliance, risk management, and governance",
             "domain_type": "supporting", "strategic_focus": "Risk and compliance"},
            {"code": "RND", "name": "Research & Development",
             "description": "Product development, innovation, and engineering",
             "domain_type": "primary", "strategic_focus": "Innovation and growth"},
        ]

        created = 0
        skipped = 0
        for domain_data in domain_defs:
            existing = db.session.execute(  # tenant-exempt: CLI command
                text("SELECT 1 FROM business_domains WHERE code = :code"),
                {"code": domain_data["code"]},
            ).scalar()
            if existing:
                skipped += 1
                continue
            domain = BusinessDomain(**domain_data)
            db.session.add(domain)
            created += 1

        db.session.commit()
        return {"success": True, "message": f"Created {created}, skipped {skipped}",
                "data": {"created": created, "skipped": skipped}}

    def _seed_vendor_json_domains(self):
        """Import vendors from domain JSON files in vendor json/ directory."""
        from app.services.vendor_json_domain_importer import VendorJsonDomainImporter
        
        importer = VendorJsonDomainImporter()
        result = importer.run(commit=True)
        
        if result.get("success"):
            data = result.get("data", {})
            message = (
                f"Imported {data.get('files_loaded', 0)} JSON files: "
                f"{data.get('vendors_created', 0)} vendors created, "
                f"{data.get('vendors_updated', 0)} updated, "
                f"{data.get('products_created', 0)} products created"
            )
            if data.get('errors', 0) > 0:
                message += f" ({data.get('errors')} errors)"
            
            return {"success": True, "message": message, "data": data}
        else:
            return result

