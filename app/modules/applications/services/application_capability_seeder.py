"""

Application Capability Mapping Seeding Service

Manages creation and updates of application capability mappings.
Validates that referenced entities exist before creating mappings.
Two-pass approach: validates then creates.
"""

from datetime import datetime
from typing import Dict, List

from sqlalchemy.exc import IntegrityError

from app.models import (
    ApplicationCapabilityMapping,
    ApplicationComponent,
    TechnicalCapability,
    UnifiedCapability,
    db,
)

from app.services.application_capability_seed_data import APPLICATION_CAPABILITY_MAPPINGS


class ApplicationCapabilitySeeder:
    """Seeds application capability mappings with validation."""

    def __init__(self):
        self.created_count = 0
        self.updated_count = 0
        self.error_count = 0
        self.validation_errors = []

    def seed(self) -> Dict:
        """
        Seed application capability mappings.

        Returns:
            Dict with created, updated, error counts and validation results
        """
        try:
            # Validate all references exist
            self._validate_references()

            if self.validation_errors:
                return {
                    "status": "validation_failed",
                    "message": "Some references could not be resolved",
                    "validation_errors": self.validation_errors,
                    "created": 0,
                    "updated": 0,
                    "errors": len(self.validation_errors),
                }

            # Create mappings
            self._create_mappings()

            return {
                "status": "success",
                "created": self.created_count,
                "updated": self.updated_count,
                "errors": self.error_count,
                "message": f"Created {self.created_count} application capability mappings, updated {self.updated_count}",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "created": self.created_count,
                "updated": self.updated_count,
                "errors": self.error_count,
            }

    def _validate_references(self):
        """Validate all referenced entities exist."""
        for mapping_data in APPLICATION_CAPABILITY_MAPPINGS:
            # Check application exists
            app = ApplicationComponent.query.filter_by(
                code=mapping_data.get("application_code")
            ).first()
            if not app:
                self.validation_errors.append(
                    f"Application '{mapping_data.get('application_code')}' not found for mapping '{mapping_data['code']}'"
                )
                self.error_count += 1

            # Check business capability exists
            biz_cap = UnifiedCapability.query.filter_by(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                code=mapping_data.get("business_capability_code")
            ).first()
            if not biz_cap:
                self.validation_errors.append(
                    f"Business Capability '{mapping_data.get('business_capability_code')}' not found for mapping '{mapping_data['code']}'"
                )
                self.error_count += 1

            # Check technical capability exists
            tech_cap = TechnicalCapability.query.filter_by(
                code=mapping_data.get("technical_capability_code")
            ).first()
            if not tech_cap:
                self.validation_errors.append(
                    f"Technical Capability '{mapping_data.get('technical_capability_code')}' not found for mapping '{mapping_data['code']}'"
                )
                self.error_count += 1

    def _create_mappings(self):
        """Create application capability mappings."""
        for mapping_data in APPLICATION_CAPABILITY_MAPPINGS:
            # Resolve references
            app = ApplicationComponent.query.filter_by(
                code=mapping_data.get("application_code")
            ).first()
            biz_cap = UnifiedCapability.query.filter_by(  # retired-ok: seeder or importer: must see retired rows so it does not recreate a duplicate
                code=mapping_data.get("business_capability_code")
            ).first()
            tech_cap = TechnicalCapability.query.filter_by(
                code=mapping_data.get("technical_capability_code")
            ).first()

            if not app or not biz_cap or not tech_cap:
                self.error_count += 1
                continue

            # Check if exists
            code = mapping_data["code"]
            # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
            record = ApplicationCapabilityMapping.query.filter_by(code=code).first()

            if not record:
                # Create new
                record = ApplicationCapabilityMapping(
                    code=code,
                    name=mapping_data["name"],
                    specialization_type="APPLICATION",
                    application_component_id=app.id,
                    business_capability_id=biz_cap.id,
                    # Note: ApplicationCapabilityMapping doesn't have technical_capability_id directly
                    # It maps app to business capability only
                    # Technical connection is implicit through the business capability
                    coverage_percentage=mapping_data.get("coverage_percentage", 0),
                    support_level=mapping_data.get("support_level", "partial"),
                    implementation_pattern=mapping_data.get("implementation_pattern"),
                    status=mapping_data.get("status", "active"),
                    relationship_type="implements",
                    is_active=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                self.created_count += 1
            else:
                # Update existing
                record.name = mapping_data["name"]
                record.coverage_percentage = mapping_data.get("coverage_percentage", 0)
                record.support_level = mapping_data.get("support_level", "partial")
                record.implementation_pattern = mapping_data.get("implementation_pattern")
                record.status = mapping_data.get("status", "active")
                record.updated_at = datetime.utcnow()
                self.updated_count += 1

            db.session.add(record)
            try:
                db.session.flush()
            except IntegrityError as e:
                db.session.rollback()
                self.error_count += 1
                print(f"Error creating mapping {code}: {e}")

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Error committing mappings: {e}")
            self.error_count += 1

    def get_application_mappings(self, application_code: str) -> List[Dict]:
        """Get all capability mappings for an application."""
        app = ApplicationComponent.query.filter_by(code=application_code).first()
        if not app:
            return []

        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        mappings = ApplicationCapabilityMapping.query.filter_by(
            application_component_id=app.id
        ).all()

        result = []
        for mapping in mappings:
            result.append(
                {
                    "code": mapping.code,
                    "name": mapping.name,
                    "business_capability": mapping.business_capability.name
                    if mapping.business_capability
                    else "?",
                    "coverage_percentage": mapping.coverage_percentage,
                    "support_level": mapping.support_level,
                    "status": mapping.status,
                }
            )

        return result
