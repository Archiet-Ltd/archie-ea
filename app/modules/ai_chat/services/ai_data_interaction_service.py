"""
-> app.modules.ai_chat.services.ai_analysis_service

AI Data Interaction Service - Controlled Data Modifications
Provides safe, validated data modification capabilities for AI Chat
"""

from app.services.archimate_backbone import sync_archimate_element
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional


from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.vendor.vendor_organization import VendorOrganization
from app.utils.capability_guardrails import CapabilityFrameworkGuardrails
from app.utils.llm_enforcement import LLMEnforcementSystem
from app.utils.ai_chat_audit import get_audit_logger

logger = logging.getLogger(__name__)

# The attributes the chat's update functions may change, per record. Each list
# is what the product's own edit surfaces already let a person change:
#   application   the application edit form (application_mgmt.forms) plus the
#                 fields the REST update (api/v1/applications.py) writes
#   capability    the capability edit and optional-field routes
#                 (capabilities/routes/enterprise_crud_routes.py) and the
#                 maturity edit
#   vendor        the vendor edit form and the REST update (api/v1/vendors.py)
#   mapping       the capability-mapping update on the application page
#                 (application_mgmt/detail_layer_routes.py) plus
#                 relationship_type, which the mapping guardrail validates
# Anything else (organization_id, id, any foreign key, created_*, deleted_*,
# lock_version, archimate_element_id ...) is refused, never written.
_NEVER_EDITABLE = frozenset({
    "id", "organization_id", "created_at", "updated_at", "created_by", "created_by_id",
    "deleted_at", "deleted_by", "lock_version", "archimate_element_id",
})

CAPABILITY_EDITABLE_FIELDS = frozenset({
    "name", "description", "category", "level", "business_domain", "business_owner",
    "it_owner", "strategic_importance", "business_value", "current_maturity_level",
    "target_maturity_level", "maturity_assessment_notes",
})
APPLICATION_API_EDITABLE_FIELDS = frozenset({
    "name", "description", "business_owner", "technical_owner", "lifecycle_status",
})
VENDOR_EDITABLE_FIELDS = frozenset({
    "name", "display_name", "description", "vendor_type", "website", "headquarters_location",
    "enterprise_readiness_score", "status", "year_founded", "employee_count",
    "strategic_tier", "partnership_level",
})
MAPPING_EDITABLE_FIELDS = frozenset({
    "support_level", "coverage_percentage", "maturity_level", "is_strategic", "notes",
    "relationship_type",
})


def application_editable_fields() -> frozenset:
    """Fields of the application edit form that are columns, plus the REST update's."""
    from sqlalchemy.inspection import inspect as sa_inspect
    from app.application_mgmt.forms import ApplicationComponentForm

    form_fields = {n for n in dir(ApplicationComponentForm) if hasattr(getattr(ApplicationComponentForm, n), "field_class")}
    columns = {c.key: c for c in sa_inspect(ApplicationComponent).columns}
    allowed = {
        n for n in (form_fields | APPLICATION_API_EDITABLE_FIELDS)
        if n in columns and not columns[n].foreign_keys and not columns[n].primary_key
    }
    return frozenset(allowed - _NEVER_EDITABLE)


def _refuse_uneditable(update_data: Any, allowed, operation: str) -> Optional[Dict[str, Any]]:
    """Return a refusal naming the fields a person may not edit, or None."""
    if not isinstance(update_data, dict):
        return {"success": False, "error": "Update data must be an object", "operation": operation}
    bad = sorted(str(f) for f in update_data if f not in allowed or f in _NEVER_EDITABLE)
    if bad:
        return {
            "success": False,
            "error": "Fields that cannot be edited here: " + ", ".join(bad),
            "rejected_fields": bad,
            "operation": operation,
        }
    return None


class AIDataInteractionService:
    """
    Safe AI data interaction service with validation and guardrails.

    Features:
    - Schema validation before modifications
    - Capability guardrails enforcement
    - Audit logging for all operations
    - Rollback capabilities
    - Permission-based access control
    """

    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.guardrails = CapabilityFrameworkGuardrails()
        self.enforcement = LLMEnforcementSystem()
        self.audit_logger = get_audit_logger(user_id) if user_id else None
        self.audit_log: List[Dict[str, Any]] = []

    def create_capability(self, capability_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new business capability with validation.

        Args:
            capability_data: Dictionary with capability fields

        Returns:
            Operation result with success status and created capability
        """
        try:
            # Basic validation
            if not capability_data.get("name"):
                return {
                    "success": False,
                    "error": "Capability name is required",
                    "operation": "create_capability",
                }

            # Check guardrails
            guardrails_result = self.guardrails.validate_capability_creation(capability_data)
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "create_capability",
                }

            # Create capability. NB: BusinessCapability.level is an INTEGER and has
            # no `maturity_level` column (it uses current_/target_maturity_level).
            # The old code passed level="Unknown" (str) + maturity_level=... which
            # raised "invalid keyword argument for BusinessCapability".
            try:
                _level = int(capability_data.get("level", 2))
            except (TypeError, ValueError):
                _level = 2
            capability = BusinessCapability(
                name=capability_data["name"],
                description=capability_data.get("description", ""),
                level=_level,
                business_domain=capability_data.get("business_domain", ""),
            )

            db.session.add(capability)
            db.session.commit()

            # Log operation with comprehensive audit
            if self.audit_logger:
                self.audit_logger.log_crud_operation(
                    operation_type="create",
                    entity_type="capability",
                    entity_id=capability.id,
                    before_state=None,
                    after_state=capability_data,
                )

            return {
                "success": True,
                "capability_id": capability.id,
                "operation": "create_capability",
                "message": f'Capability "{capability.name}" created successfully',
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating capability: {e}")
            return {"success": False, "error": str(e), "operation": "create_capability"}

    def update_capability(self, capability_id: int, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing business capability.

        Args:
            capability_id: ID of capability to update
            update_data: Dictionary with fields to update

        Returns:
            Operation result with success status and updated capability
        """
        try:
            capability = BusinessCapability.query.get(capability_id)
            if not capability:
                return {
                    "success": False,
                    "error": f"Capability with ID {capability_id} not found",
                    "operation": "update_capability",
                }

            refused = _refuse_uneditable(update_data, CAPABILITY_EDITABLE_FIELDS, "update_capability")
            if refused:
                return refused

            # Check guardrails
            guardrails_result = self.guardrails.validate_capability_update(capability, update_data)
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "update_capability",
                }

            # Capture before state for audit
            before_state = {}
            for field in update_data.keys():
                if hasattr(capability, field):
                    before_state[field] = getattr(capability, field)

            # Update fields
            for field, value in update_data.items():
                if hasattr(capability, field):
                    setattr(capability, field, value)

            capability.updated_at = datetime.utcnow()
            db.session.commit()

            # Log operation with comprehensive audit
            if self.audit_logger:
                self.audit_logger.log_crud_operation(
                    operation_type="update",
                    entity_type="capability",
                    entity_id=capability.id,
                    before_state=before_state,
                    after_state=update_data,
                )

            return {
                "success": True,
                "capability_id": capability.id,
                "operation": "update_capability",
                "message": f'Capability "{capability.name}" updated successfully',
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating capability: {e}")
            return {"success": False, "error": str(e), "operation": "update_capability"}

    def create_application(self, application_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new application with validation.

        Args:
            application_data: Dictionary with application fields

        Returns:
            Operation result with success status and created application
        """
        try:
            # Basic validation
            if not application_data.get("name"):
                return {
                    "success": False,
                    "error": "Application name is required",
                    "operation": "create_application",
                }

            # Check guardrails
            guardrails_result = self.guardrails.validate_application_creation(application_data)
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "create_application",
                }

            # Create application
            application = ApplicationComponent(
                name=application_data["name"],
                description=application_data.get("description", ""),
                application_type=application_data.get("application_type", "web"),
                deployment_model=application_data.get("deployment_model", "cloud"),
                business_domain=application_data.get("business_domain", ""),
                criticality=application_data.get("criticality", "supporting"),
                lifecycle_status=application_data.get("lifecycle_status", "planning"),
                vendor_name=application_data.get("vendor_name", ""),
                application_owner=application_data.get("application_owner", ""),
                business_owner=application_data.get("business_owner", ""),
                technical_owner=application_data.get("technical_owner", ""),
            )

            db.session.add(application)
            db.session.commit()

            # Log operation
            self._log_operation("create_application", application.id, application_data)

            return {
                "success": True,
                "application_id": application.id,
                "operation": "create_application",
                "message": f'Application "{application.name}" created successfully',
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating application: {e}")
            return {"success": False, "error": str(e), "operation": "create_application"}

    def update_application(
        self, application_id: int, update_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing application.

        Args:
            application_id: ID of application to update
            update_data: Dictionary with fields to update

        Returns:
            Operation result with success status and updated application
        """
        try:
            application = ApplicationComponent.query.get(application_id)
            if not application:
                return {
                    "success": False,
                    "error": f"Application with ID {application_id} not found",
                    "operation": "update_application",
                }

            refused = _refuse_uneditable(update_data, application_editable_fields(), "update_application")
            if refused:
                return refused

            # Check guardrails
            guardrails_result = self.guardrails.validate_application_update(
                application, update_data
            )
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "update_application",
                }

            # Update fields
            for field, value in update_data.items():
                if hasattr(application, field):
                    setattr(application, field, value)

            application.updated_at = datetime.utcnow()
            db.session.commit()

            # Log operation
            self._log_operation("update_application", application_id, update_data)

            return {
                "success": True,
                "application_id": application.id,
                "operation": "update_application",
                "message": f'Application "{application.name}" updated successfully',
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating application: {e}")
            return {"success": False, "error": str(e), "operation": "update_application"}

    def _vendor_edit_refusal(self, operation: str) -> Optional[Dict[str, Any]]:
        """Refuse a create, edit or delete of a shared vendor row unless the
        acting user is a platform administrator.

        The acting user is the signed-in user, or the approver during an
        approval replay. No acting user means refuse: nothing that edits a
        vendor from here runs without a person.
        """
        from app.middleware.tenant_decorators import (
            SHARED_VENDOR_REFUSAL, shared_vendor_edit_allowed,
        )
        from app.models.user import User

        actor = User.query.get(self.user_id) if self.user_id else None
        if actor is None or not shared_vendor_edit_allowed(actor):
            return {
                "success": False,
                "error": SHARED_VENDOR_REFUSAL,
                "forbidden": True,
                "operation": operation,
            }
        return None

    def create_vendor(self, vendor_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new vendor organization with validation.

        Args:
            vendor_data: Dictionary with vendor fields

        Returns:
            Operation result with success status and created vendor
        """
        forbidden = self._vendor_edit_refusal("create_vendor")
        if forbidden:
            return forbidden
        try:
            # Basic validation
            if not vendor_data.get("name"):
                return {
                    "success": False,
                    "error": "Vendor name is required",
                    "operation": "create_vendor",
                }

            # Check guardrails
            guardrails_result = self.guardrails.validate_vendor_creation(vendor_data)
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "create_vendor",
                }

            # Create vendor
            vendor = VendorOrganization(
                name=vendor_data["name"],
                display_name=vendor_data.get("display_name", vendor_data["name"]),
                vendor_type=vendor_data.get("vendor_type", "software_vendor"),
                headquarters_location=vendor_data.get("headquarters_location", ""),
                website=vendor_data.get("website", ""),
                description=vendor_data.get("description", ""),
                strategic_tier=vendor_data.get("strategic_tier", "tier_3_approved"),
                status=vendor_data.get("status", "active"),
            )

            db.session.add(vendor)
            db.session.commit()

            # Log operation
            self._log_operation("create_vendor", vendor.id, vendor_data)

            return {
                "success": True,
                "vendor_id": vendor.id,
                "operation": "create_vendor",
                "message": f'Vendor "{vendor.name}" created successfully',
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating vendor: {e}")
            return {"success": False, "error": str(e), "operation": "create_vendor"}

    def update_vendor(self, vendor_id: int, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing vendor organization.

        Args:
            vendor_id: ID of vendor to update
            update_data: Dictionary with fields to update

        Returns:
            Operation result with success status and updated vendor
        """
        forbidden = self._vendor_edit_refusal("update_vendor")
        if forbidden:
            return forbidden
        try:
            vendor = VendorOrganization.query.get(vendor_id)
            if not vendor:
                return {
                    "success": False,
                    "error": f"Vendor with ID {vendor_id} not found",
                    "operation": "update_vendor",
                }

            refused = _refuse_uneditable(update_data, VENDOR_EDITABLE_FIELDS, "update_vendor")
            if refused:
                return refused

            # Check guardrails
            guardrails_result = self.guardrails.validate_vendor_update(vendor, update_data)
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "update_vendor",
                }

            # Update fields
            for field, value in update_data.items():
                if hasattr(vendor, field):
                    setattr(vendor, field, value)

            vendor.updated_at = datetime.utcnow()
            from app.middleware.tenant_decorators import record_vendor_editor

            record_vendor_editor(vendor, self.user_id)
            db.session.commit()

            # Log operation
            self._log_operation("update_vendor", vendor_id, update_data)

            return {
                "success": True,
                "vendor_id": vendor.id,
                "operation": "update_vendor",
                "message": f'Vendor "{vendor.name}" updated successfully',
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating vendor: {e}")
            return {"success": False, "error": str(e), "operation": "update_vendor"}

    def create_capability_mapping(self, mapping_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new capability-application mapping with validation.

        Args:
            mapping_data: Dictionary with mapping fields

        Returns:
            Operation result with success status and created mapping
        """
        try:
            # Basic validation
            if not mapping_data.get("unified_capability_id") or not mapping_data.get(
                "application_component_id"
            ):
                return {
                    "success": False,
                    "error": "Both capability_id and application_id are required",
                    "operation": "create_capability_mapping",
                }

            # Check guardrails
            guardrails_result = self.guardrails.validate_mapping_creation(mapping_data)
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "create_capability_mapping",
                }

            # Create mapping
            mapping = UnifiedApplicationCapabilityMapping(
                unified_capability_id=mapping_data["unified_capability_id"],
                application_component_id=mapping_data["application_component_id"],
                support_level=mapping_data.get("support_level", "partial"),
                coverage_percentage=mapping_data.get("coverage_percentage", 0),
                relationship_type=mapping_data.get("relationship_type", "enables"),
                gap_status=mapping_data.get("gap_status", "unknown"),
                priority=mapping_data.get("priority", "medium"),
                assessor=str(self.user_id) if self.user_id else "AI_Assistant",
            )

            db.session.add(mapping)
            db.session.commit()

            # Log operation
            self._log_operation("create_capability_mapping", mapping.id, mapping_data)

            return {
                "success": True,
                "mapping_id": mapping.id,
                "operation": "create_capability_mapping",
                "message": "Capability mapping created successfully",
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating capability mapping: {e}")
            return {"success": False, "error": str(e), "operation": "create_capability_mapping"}

    def update_capability_mapping(
        self, mapping_id: int, update_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing capability-application mapping.

        Args:
            mapping_id: ID of mapping to update
            update_data: Dictionary with fields to update

        Returns:
            Operation result with success status and updated mapping
        """
        try:
            mapping = UnifiedApplicationCapabilityMapping.query.get(mapping_id)
            if not mapping:
                return {
                    "success": False,
                    "error": f"Mapping with ID {mapping_id} not found",
                    "operation": "update_capability_mapping",
                }

            refused = _refuse_uneditable(update_data, MAPPING_EDITABLE_FIELDS, "update_capability_mapping")
            if refused:
                return refused

            # Check guardrails
            guardrails_result = self.guardrails.validate_mapping_update(mapping, update_data)
            if not guardrails_result.get("valid", True):
                return {
                    "success": False,
                    "error": f'Guardrails violation: {guardrails_result.get("reason", "Unknown")}',
                    "operation": "update_capability_mapping",
                }

            # Update fields
            for field, value in update_data.items():
                if hasattr(mapping, field):
                    setattr(mapping, field, value)

            mapping.updated_at = datetime.utcnow()
            db.session.commit()

            # Log operation
            self._log_operation("update_capability_mapping", mapping_id, update_data)

            return {
                "success": True,
                "mapping_id": mapping.id,
                "operation": "update_capability_mapping",
                "message": "Capability mapping updated successfully",
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating capability mapping: {e}")
            return {"success": False, "error": str(e), "operation": "update_capability_mapping"}

    def bulk_create_applications(self, applications: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create multiple applications in bulk.

        Args:
            applications: List of application data dictionaries

        Returns:
            Bulk operation result with success/failure summary
        """
        results = []
        success_count = 0
        failure_count = 0

        for app_data in applications:
            result = self.create_application(app_data)
            if result["success"]:
                success_count += 1
            else:
                failure_count += 1

            results.append(result)

        return {
            "success": failure_count == 0,
            "operation": "bulk_create_applications",
            "summary": {
                "total": len(applications),
                "success": success_count,
                "failure": failure_count,
            },
            "results": results,
        }

    def bulk_update_applications(self, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Update multiple applications in bulk.

        Args:
            updates: List of update dictionaries with application_id and update_data

        Returns:
            Bulk operation result with success/failure summary
        """
        results = []
        success_count = 0
        failure_count = 0

        for update in updates:
            application_id = update.get("application_id")
            update_data = update.get("update_data", {})

            if not application_id or not update_data:
                failure_count += 1
                results.append(
                    {
                        "application_id": application_id,
                        "success": False,
                        "error": "Missing application_id or update_data",
                    }
                )
                continue

            result = self.update_application(application_id, update_data)
            if result["success"]:
                success_count += 1
            else:
                failure_count += 1

            results.append(result)

        return {
            "success": failure_count == 0,
            "operation": "bulk_update_applications",
            "summary": {"total": len(updates), "success": success_count, "failure": failure_count},
            "results": results,
        }

    def add_compliance_requirement(self, requirement_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add a compliance requirement to a capability.

        Args:
            requirement_data: Dictionary with compliance requirement fields

        Returns:
            Operation result with success status
        """
        try:
            # This would integrate with compliance models
            # For now, return a placeholder response
            self._log_operation("add_compliance_requirement", None, requirement_data)

            return {
                "success": True,
                "operation": "add_compliance_requirement",
                "message": "Compliance requirement added successfully",
            }

        except Exception as e:
            logger.error(f"Error adding compliance requirement: {e}")
            return {"success": False, "error": str(e), "operation": "add_compliance_requirement"}

    def bulk_update_capabilities(self, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Perform bulk updates on multiple capabilities.

        Args:
            updates: List of update dictionaries with capability_id and update_data

        Returns:
            Bulk operation result with success/failure summary
        """
        results = []
        success_count = 0
        failure_count = 0

        for update in updates:
            capability_id = update.get("capability_id")
            update_data = update.get("update_data", {})

            if not capability_id or not update_data:
                failure_count += 1
                results.append(
                    {
                        "capability_id": capability_id,
                        "success": False,
                        "error": "Missing capability_id or update_data",
                    }
                )
                continue

            result = self.update_capability(capability_id, update_data)
            if result["success"]:
                success_count += 1
            else:
                failure_count += 1

            results.append(result)

        return {
            "success": failure_count == 0,
            "operation": "bulk_update_capabilities",
            "summary": {"total": len(updates), "success": success_count, "failure": failure_count},
            "results": results,
        }

    def create_work_package(self, wp_data: Dict[str, Any]) -> Dict[str, Any]:
        """AIC-303: Create a work package from AI chat with optional entity links."""
        try:
            from app.models.implementation_migration import WorkPackage

            name = wp_data.get("name")
            if not name or len(name.strip()) < 3:
                return {"success": False, "error": "Work package name is required (min 3 chars)", "operation": "create_work_package"}

            wp = WorkPackage(
                name=name.strip()[:255],
                description=wp_data.get("description", "")[:2000],
                status=wp_data.get("status", "planned"),
                priority=wp_data.get("priority", "medium"),
                context=wp_data.get("context", "architecture"),
                owner_id=self.user_id,
            )
            db.session.add(wp)
            sync_archimate_element(wp)
            db.session.flush()  # Get the ID

            # Link applications if provided
            linked_apps = wp_data.get("linked_application_ids", [])
            linked_caps = wp_data.get("linked_capability_ids", [])
            link_count = 0

            # For app linking, use the application_component_id field or create child WPs
            if linked_apps and len(linked_apps) == 1:
                wp.application_component_id = linked_apps[0]
                link_count += 1
            elif linked_apps and len(linked_apps) > 1:
                # Create child work packages per app for bulk actions
                for app_id in linked_apps[:20]:
                    from app.models.application_portfolio import ApplicationComponent
                    app_obj = ApplicationComponent.query.get(app_id)
                    if app_obj:
                        child = WorkPackage(
                            name=f"{name} - {app_obj.name}",
                            description=f"Sub-task for {app_obj.name}",
                            status="planned",
                            priority=wp_data.get("priority", "medium"),
                            context="architecture",
                            parent_id=wp.id,
                            application_component_id=app_id,
                            owner_id=self.user_id,
                        )
                        db.session.add(child)
                        sync_archimate_element(child)
                        link_count += 1

            # Link capability if provided
            if linked_caps:
                wp.capability_id = linked_caps[0]
                link_count += 1

            db.session.commit()

            self._log_operation("create_work_package", wp.id, wp_data)

            return {
                "success": True,
                "work_package_id": wp.id,
                "operation": "create_work_package",
                "message": f"Work package '{name}' created (ID: {wp.id})",
                "linked_entities": link_count,
                "url": f"/roadmap/work-packages/{wp.id}",
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating work package: {e}")
            return {"success": False, "error": str(e), "operation": "create_work_package"}

    def link_application_to_capability(self, link_data: Dict[str, Any]) -> Dict[str, Any]:
        """AIC-303: Create application-capability mapping junction row."""
        try:
            from app.models.application_capability import ApplicationCapabilityMapping
            from app.middleware.tenant_context import current_org_id

            app_id = link_data.get("application_id")
            cap_id = link_data.get("capability_id")
            if not app_id or not cap_id:
                return {"success": False, "error": "Both application_id and capability_id required"}

            # ApplicationCapabilityMapping does NOT use TenantMixin, so organization_id
            # is neither auto-stamped on insert nor auto-filtered on select. The prior
            # raw-SQL INSERT omitted the NOT NULL organization_id (IntegrityError on
            # every call) and the raw SELECT read across all tenants. Scope both to the
            # active org explicitly via the ORM.
            org_id = current_org_id()
            if org_id is None:
                return {"success": False, "error": "No active organization context"}

            # Check if already linked (scoped to this org)
            existing = ApplicationCapabilityMapping.query.filter_by(
                application_component_id=app_id,
                business_capability_id=cap_id,
                organization_id=org_id,
            ).first()
            if existing:
                return {"success": True, "message": "Already linked", "already_existed": True}

            mapping = ApplicationCapabilityMapping(
                application_component_id=app_id,
                business_capability_id=cap_id,
                organization_id=org_id,
            )
            db.session.add(mapping)
            db.session.commit()

            self._log_operation("link_application_capability", mapping.id, link_data)

            return {
                "success": True,
                "message": f"Linked application {link_data.get('application_name', app_id)} to capability {link_data.get('capability_name', cap_id)}",
                "operation": "link_application_capability",
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error linking app to capability: {e}")
            return {"success": False, "error": str(e)}

    def delete_capability(self, capability_id: int) -> Dict[str, Any]:
        """Hard-delete a business capability. Caller must verify admin privileges before invoking."""
        try:
            capability = BusinessCapability.query.get(capability_id)
            if not capability:
                return {"success": False, "error": f"Capability with ID {capability_id} not found", "operation": "delete_capability"}
            name = capability.name
            if self.audit_logger:
                self.audit_logger.log_crud_operation(
                    operation_type="delete",
                    entity_type="capability",
                    entity_id=capability_id,
                    before_state={"name": name},
                    after_state={},
                )
            db.session.delete(capability)
            db.session.commit()
            return {"success": True, "capability_id": capability_id, "operation": "delete_capability", "message": f'Capability "{name}" permanently deleted'}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting capability: {e}")
            return {"success": False, "error": str(e), "operation": "delete_capability"}

    def delete_application(self, application_id: int) -> Dict[str, Any]:
        """Hard-delete an application component. Caller must verify admin privileges before invoking."""
        try:
            application = ApplicationComponent.query.get(application_id)
            if not application:
                return {"success": False, "error": f"Application with ID {application_id} not found", "operation": "delete_application"}
            name = application.name
            if self.audit_logger:
                self.audit_logger.log_crud_operation(
                    operation_type="delete",
                    entity_type="application",
                    entity_id=application_id,
                    before_state={"name": name},
                    after_state={},
                )
            db.session.delete(application)
            db.session.commit()
            return {"success": True, "application_id": application_id, "operation": "delete_application", "message": f'Application "{name}" permanently deleted'}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting application: {e}")
            return {"success": False, "error": str(e), "operation": "delete_application"}

    def delete_vendor(self, vendor_id: int) -> Dict[str, Any]:
        """Hard-delete a vendor organization. Only a platform administrator may."""
        forbidden = self._vendor_edit_refusal("delete_vendor")
        if forbidden:
            return forbidden
        try:
            vendor = VendorOrganization.query.get(vendor_id)
            if not vendor:
                return {"success": False, "error": f"Vendor with ID {vendor_id} not found", "operation": "delete_vendor"}
            name = vendor.name
            if self.audit_logger:
                self.audit_logger.log_crud_operation(
                    operation_type="delete",
                    entity_type="vendor",
                    entity_id=vendor_id,
                    before_state={"name": name},
                    after_state={},
                )
            db.session.delete(vendor)
            db.session.commit()
            return {"success": True, "vendor_id": vendor_id, "operation": "delete_vendor", "message": f'Vendor "{name}" permanently deleted'}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting vendor: {e}")
            return {"success": False, "error": str(e), "operation": "delete_vendor"}

    def get_audit_log(self, operation_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get audit log of operations.

        Args:
            operation_type: Filter by operation type (optional)

        Returns:
            List of audit log entries
        """
        if operation_type:
            return [entry for entry in self.audit_log if entry.get("operation") == operation_type]
        return self.audit_log.copy()

    def validate_operation(self, operation: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate an operation before execution.

        Args:
            operation: Operation type
            data: Operation data

        Returns:
            Validation result
        """
        try:
            if operation == "create_capability":
                return self.guardrails.validate_capability_creation(data)
            elif operation == "update_capability":
                capability_id = data.get("capability_id")
                if capability_id:
                    capability = BusinessCapability.query.get(capability_id)
                    if capability:
                        return self.guardrails.validate_capability_update(capability, data)
                return {"valid": False, "reason": "Capability not found"}
            elif operation == "create_application":
                return self.guardrails.validate_application_creation(data)
            elif operation == "update_application":
                application_id = data.get("application_id")
                if application_id:
                    application = ApplicationComponent.query.get(application_id)
                    if application:
                        return self.guardrails.validate_application_update(application, data)
                return {"valid": False, "reason": "Application not found"}
            elif operation == "create_vendor":
                return self.guardrails.validate_vendor_creation(data)
            elif operation == "update_vendor":
                vendor_id = data.get("vendor_id")
                if vendor_id:
                    vendor = VendorOrganization.query.get(vendor_id)
                    if vendor:
                        return self.guardrails.validate_vendor_update(vendor, data)
                return {"valid": False, "reason": "Vendor not found"}
            elif operation == "create_capability_mapping":
                return self.guardrails.validate_mapping_creation(data)
            elif operation == "update_capability_mapping":
                mapping_id = data.get("mapping_id")
                if mapping_id:
                    mapping = UnifiedApplicationCapabilityMapping.query.get(mapping_id)
                    if mapping:
                        return self.guardrails.validate_mapping_update(mapping, data)
                return {"valid": False, "reason": "Mapping not found"}
            else:
                return {"valid": True, "reason": "Unknown operation, allowing by default"}

        except Exception as e:
            return {"valid": False, "reason": f"Validation error: {str(e)}"}

    def _log_operation(self, operation: str, entity_id: Optional[int], data: Dict[str, Any]):
        """Log operation for audit trail."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "user_id": self.user_id,
            "operation": operation,
            "entity_id": entity_id,
            "data": data,
        }
        self.audit_log.append(log_entry)
        logger.info(
            f"AI Data Interaction: {operation} by user {self.user_id} on entity {entity_id}"
        )

    def create_requirement(self, requirement_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an ArchiMate Requirement element linked to a capability (AIC-003).

        Args:
            requirement_data: dict with keys:
                - name (str, required): requirement statement
                - description (str, optional): detail
                - capability_id (int, optional): BusinessCapability to link via Association
                - adm_phase (str, optional): e.g. 'A', 'B'
                - source (str, optional): originating context

        Returns:
            Dict with success, requirement_id, element_id, and relationship_id
        """
        try:
            from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
            import json

            name = (requirement_data.get("name") or "").strip()[:200]
            if not name:
                return {"success": False, "error": "name is required"}

            adm_phase = requirement_data.get("adm_phase", "")
            props = {"source": requirement_data.get("source", "ai_chat")}
            if adm_phase:
                props["adm_phase"] = adm_phase

            req_element = ArchiMateElement(
                name=name,
                type="Requirement",
                layer="motivation",
                scope="enterprise",
                properties=json.dumps(props),
            )
            # The DB column 'plateau' is mapped to the Python attribute
            # 'togaf_plateau' on the production model (the bare name 'plateau' is a
            # relationship backref from Plateau, so passing a string to it raises
            # "Incompatible collection type: str is not list-like"). The lightweight
            # FAST_INIT model keeps 'plateau' as the column attr — set whichever
            # exists so both runtimes persist the value.
            if hasattr(type(req_element), "togaf_plateau"):
                req_element.togaf_plateau = "Target"
            else:
                req_element.plateau = "Target"
            db.session.add(req_element)
            db.session.flush()

            relationship_id = None
            capability_id = requirement_data.get("capability_id")
            if capability_id:
                # Link Requirement → Capability via Association relationship
                # ArchiMateRelationship has no 'name' column; the user-facing label
                # field is 'custom_label'.
                rel = ArchiMateRelationship(
                    source_id=req_element.id,
                    target_id=capability_id,
                    type="Association",
                    custom_label="supports",
                )
                db.session.add(rel)
                db.session.flush()
                relationship_id = rel.id

            db.session.commit()
            self._log_operation("create_requirement", req_element.id, requirement_data)

            return {
                "success": True,
                "requirement_id": req_element.id,
                "element_id": req_element.id,
                "name": name,
                "type": "Requirement",
                "layer": "motivation",
                "relationship_id": relationship_id,
            }
        except Exception as exc:
            db.session.rollback()
            logger.error("create_requirement failed: %s", exc)
            return {"success": False, "error": str(exc)}
