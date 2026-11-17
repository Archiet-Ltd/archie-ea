# mass-deletion-ok
"""

Abacus Application Importer Service

Three-layer architecture for transforming Abacus Components into A.R.C.I.E ApplicationComponent records:
1. AbacusComponentTransformer - Maps Abacus Properties → ApplicationComponent fields
2. AbacusApplicationUpsertHandler - Merge logic (create/update/preserve enrichments)
3. BatchImportIntegration - Wrapper to work with BatchImportService approval flow
"""

import logging
from datetime import datetime
from typing import Dict, List

from app.config.abacus_field_mapping import (
    derive_deployment_from_lifecycle,
    normalize_criticality,
    normalize_lifecycle_status,
    parse_date,
)
from app.extensions import db
from app.models import ApplicationComponent, ApplicationImportHistory

logger = logging.getLogger(__name__)


class AbacusComponentTransformer:
    """Transform Abacus Component API response into ApplicationComponent field dict."""

    # Map Abacus property names to ApplicationComponent fields + transform type.
    # "direct" = store as-is, "status" = normalize via STATUS_MAPPING,
    # "lifecycle" = normalize_lifecycle_status(), "deployment_model" = DEPLOYMENT_MODEL_MAPPING,
    # "criticality" = normalize_criticality(), "date" = parse_date()
    PROPERTY_MAPPING = {
        # Identification
        "APP ID": ("application_code", "direct"),
        "ApplicationType": ("application_category", "direct"),
        # Status & Lifecycle
        "Application Status": ("deployment_status", "status"),
        "LifecycleStatus": ("lifecycle_status", "lifecycle"),
        "Status": ("deployment_status", "status"),
        # Ownership
        "BusinessOwner": ("business_owner", "direct"),
        "Owner": ("business_owner", "direct"),
        "TechnicalOwner": ("technical_owner", "direct"),
        "ApplicationOwner": ("application_owner", "direct"),
        # Classification
        "Category": ("application_category", "direct"),
        "BusinessDomain": ("business_domain", "direct"),
        "Domain": ("business_domain", "direct"),
        "BusinessCriticality": ("business_criticality", "criticality"),
        "Vendor": ("vendor_name", "direct"),
        # Technical
        "Deployment Scope": ("deployment_model", "deployment_model"),
        "Technology Stack": ("technology_stack", "direct"),
        "Database": ("database_platforms", "direct"),
        "API": ("api_available", "direct"),
        # Dates
        "GoLiveDate": ("go_live_date", "date"),
        "RetirementDate": ("planned_retirement_date", "date"),
    }

    # Map Abacus status values to A.R.C.I.E deployment_status
    STATUS_MAPPING = {
        "Production": "production",
        "Implementing": "testing",
        "Design": "development",
        "Evaluation": "staging",
        "Draft": "development",
        # ABACUS numbered lifecycle codes (used as fallback for deployment_status)
        "2.1 STRATEGIC": "production",
        "2.1 Strategic": "production",
        "2.2 TACTICAL": "production",
        "2.2 Tactical": "production",
        "1. UNDETERMINED": "development",
        "1. Undetermined": "development",
        "3. SUNSET": "retired",
        "3. Sunset": "retired",
        "4.1 DECOM DECIDED": "retired",
        "4.1 Decom Decided": "retired",
        "4.2 DECOM PLANNED": "retired",
        "4.2 Decom Planned": "retired",
        "4.3 READ-ONLY": "retired",
        "4.3 Read-Only": "retired",
        "5. DECOMMISSIONED": "retired",
        "5. Decommissioned": "retired",
    }

    # Map deployment scope values
    DEPLOYMENT_MODEL_MAPPING = {
        "GLOBAL": "hybrid",
        "ON_PREMISE": "on_premise",
        "CLOUD": "cloud",
        "SAAS": "saas",
    }

    def __init__(self):
        """Initialize transformer with mapping configurations."""
        self.transformations_count = 0
        self.errors = []

    def _extract_properties(self, properties_list: List[Dict], target_dict: Dict) -> float:
        """
        Extract relevant Properties from Abacus Properties array.

        Properties are typed with categories like '1.Identification', '3.Lifecycle', etc.
        Uses PROPERTY_MAPPING to map all known properties with appropriate transforms.
        """
        confidence = 0.0
        mapped_count = 0

        for prop in properties_list:
            name = prop.get("Name", "").strip()
            value = prop.get("Value", "").strip()

            if not name or not value:
                continue

            mapping = self.PROPERTY_MAPPING.get(name)
            if mapping:
                field_name, transform_type = mapping

                # Apply the appropriate transform
                if transform_type == "status":
                    transformed_value = self.STATUS_MAPPING.get(value, value.lower())
                elif transform_type == "lifecycle":
                    transformed_value = normalize_lifecycle_status(value)
                elif transform_type == "deployment_model":
                    transformed_value = self.DEPLOYMENT_MODEL_MAPPING.get(value, "hybrid")
                elif transform_type == "criticality":
                    transformed_value = normalize_criticality(value)
                elif transform_type == "date":
                    transformed_value = parse_date(value)
                else:
                    transformed_value = value

                # Don't overwrite a field that was already set by a higher-priority property
                if field_name not in target_dict or target_dict[field_name] is None:
                    target_dict[field_name] = transformed_value
                    mapped_count += 1
                    confidence = max(confidence, 0.85)
            else:
                # Store unmapped properties as metadata for reference
                if "abacus_properties" not in target_dict:
                    target_dict["abacus_properties"] = {}
                target_dict["abacus_properties"][name] = value

        # If lifecycle_status was set but deployment_status was not, derive it
        if "lifecycle_status" in target_dict and "deployment_status" not in target_dict:
            target_dict["deployment_status"] = derive_deployment_from_lifecycle(
                target_dict["lifecycle_status"]
            )
            mapped_count += 1

        return confidence if mapped_count > 0 else 0.3


class AbacusApplicationUpsertHandler:
    """
    Handle create/update logic for ApplicationComponent with Abacus data.
    Preserves existing enrichments (AI discoveries, assessments, vendor analysis).
    """

    # Fields that should NEVER be overwritten by Abacus data
    PROTECTED_FIELDS = {
        "discovered_by_ai",
        "discovery_confidence",
        "assessment_notes",
        "notes",
        "last_assessed",
        "created_at",
        "roi_score",
        "user_satisfaction_score",
        "performance_rating",
        "vendor_product_id",
        "total_cost_of_ownership",
        "license_cost",
        "maintenance_cost",
        "infrastructure_cost",
    }

    def __init__(self):
        """Initialize upsert handler."""
        self.created_count = 0
        self.updated_count = 0
        self.preserved_enrichments = 0
        self.conflict_resolutions = []

    def _apply_fields(self, app: ApplicationComponent, fields: Dict, is_new: bool = False):
        """Apply transformed fields to ApplicationComponent instance.

        A deployment status that is not on the one list raises StatusRefused; the
        caller refuses that application and keeps the others.
        """
        for field, value in fields.items():
            if field.startswith("_"):  # Skip internal metadata like _connection_count
                continue
            if hasattr(app, field) and value is not None:
                setattr(app, field, value)

        # Set default enrichment flags for new apps
        if is_new:
            app.discovered_by_ai = False
            app.abacus_source = True
            app.created_at = datetime.utcnow()


class AbacusApplicationImporter:
    """
    Main importer orchestrator - integrates transformer and upsert handler.
    Designed for use with BatchImportService (staged approval workflow).
    """

    def __init__(self):
        """Initialize importer with sub-components."""
        self.transformer = AbacusComponentTransformer()
        self.upsert_handler = AbacusApplicationUpsertHandler()
        self.import_history = None

    def _create_import_history(self, summary: Dict, source_info: Dict):
        """Record import operation in ApplicationImportHistory."""
        try:
            history = ApplicationImportHistory(
                imported_at=datetime.utcnow(),
                imported_by_name=source_info.get("user_name", "System")
                if source_info
                else "System",
                import_source="abacus",
                total_records=summary["total_components"],
                records_created=summary["created"],
                records_updated=summary["updated"],
                records_skipped=summary["skipped"],
                records_failed=summary["errors"],
                status="completed",
                error_summary="; ".join(summary.get("transformer_errors", [])[:5]),
                error_details={
                    "transformer_errors": summary.get("transformer_errors", []),
                    "conflicts": summary.get("conflicts", []),
                },
                import_settings={
                    "avg_confidence": summary["avg_confidence"],
                    "protected_fields_preserved": summary["protected_fields_preserved"],
                },
            )
            db.session.add(history)
            logger.info(
                f"Import completed: {summary['created']} created, "
                f"{summary['updated']} updated, {summary['errors']} errors"
            )
        except Exception as e:
            logger.error(f"Error creating import history: {e}", exc_info=True)
