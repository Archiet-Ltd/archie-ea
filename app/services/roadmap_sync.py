"""
Roadmap Data Synchronization Services
Real-time synchronization between roadmap entities and related systems
"""

from app.models.roadmap_models import work_package_capabilities
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict  # dead-code-ok

from sqlalchemy import text  # dead-code-ok

from app import db
from app.utils.tenant_sql import org_scope
from app.models.roadmap_models import (  # dead-code-ok
    ImplementationGap,
    PlanningDeliverable,
    RoadmapAudit,
    RoadmapWorkPackage,
)

# Aliases for backwards compatibility
Deliverable = PlanningDeliverable
ImplementationWorkPackage = RoadmapWorkPackage
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_capability import UnifiedCapability
from app.models.vendor.vendor_organization import VendorProduct

logger = logging.getLogger(__name__)


class RoadmapDataSync:
    """Data synchronization service for roadmap entities"""

    def __init__(self):
        self.sync_batch_id = None
        self.sync_start_time = None

    def start_sync_batch(self, user_id: int, reason: str = "Scheduled sync") -> str:
        """Start a new synchronization batch"""
        self.sync_batch_id = f"sync_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{user_id}"
        self.sync_start_time = datetime.utcnow()

        logger.info(f"Started sync batch {self.sync_batch_id} for user {user_id}: {reason}")
        return self.sync_batch_id

    def sync_capabilities_to_work_packages(self) -> Dict[str, Any]:
        """
        Synchronize business capabilities with work packages

        Creates work packages for new capabilities, updates existing ones,
        and archives work packages for deleted capabilities.
        """
        try:
            if not self.sync_batch_id:
                self.start_sync_batch(1, "Capability sync")

            sync_results = {"created": 0, "updated": 0, "archived": 0, "errors": []}

            # Get all capabilities
            capabilities = UnifiedCapability.query.all()

            for capability in capabilities:
                try:
                    # Check if work package already exists
                    existing_wp = ImplementationWorkPackage.query.filter_by(
                        source_type="capability", source_id=capability.id
                    ).first()

                    if existing_wp:
                        # Update existing work package
                        updated = self._update_capability_work_package(existing_wp, capability)
                        if updated:
                            sync_results["updated"] += 1
                    else:
                        # Create new work package
                        self._create_capability_work_package(capability)
                        sync_results["created"] += 1

                except Exception as e:
                    error_msg = f"Error syncing capability {capability.id}: {str(e)}"
                    logger.error(error_msg)
                    sync_results["errors"].append(error_msg)

            # Archive work packages for deleted capabilities
            archived = self._archive_orphaned_work_packages("capability")
            sync_results["archived"] = archived

            logger.info(f"Capability sync completed: {sync_results}")
            return sync_results

        except Exception as e:
            logger.error(f"Error in capability sync: {e}")
            raise

    def sync_applications_to_deliverables(self) -> Dict[str, Any]:
        """
        Synchronize applications with deliverables

        Creates deliverables for applications that need modernization,
        updates existing deliverables, and archives orphaned ones.
        """
        try:
            if not self.sync_batch_id:
                self.start_sync_batch(1, "Application sync")

            sync_results = {"created": 0, "updated": 0, "archived": 0, "errors": []}

            # Get all applications
            applications = ApplicationComponent.query.all()

            for application in applications:
                try:
                    # Check if deliverable already exists
                    existing_deliverable = Deliverable.query.filter_by(
                        source_application_id=application.id
                    ).first()

                    if existing_deliverable:
                        # Update existing deliverable
                        updated = self._update_application_deliverable(
                            existing_deliverable, application
                        )
                        if updated:
                            sync_results["updated"] += 1
                    else:
                        # Create new deliverable if application needs modernization
                        if self._application_needs_deliverable(application):
                            self._create_application_deliverable(application)
                            sync_results["created"] += 1

                except Exception as e:
                    error_msg = f"Error syncing application {application.id}: {str(e)}"
                    logger.error(error_msg)
                    sync_results["errors"].append(error_msg)

            # Archive deliverables for deleted applications
            archived = self._archive_orphaned_deliverables()
            sync_results["archived"] = archived

            logger.info(f"Application sync completed: {sync_results}")
            return sync_results

        except Exception as e:
            logger.error(f"Error in application sync: {e}")
            raise

    def sync_gaps_to_initiatives(self) -> Dict[str, Any]:
        """
        Synchronize implementation gaps with work package initiatives

        Creates work packages for unresolved gaps, updates existing ones.
        """
        try:
            if not self.sync_batch_id:
                self.start_sync_batch(1, "Gap sync")

            sync_results = {"created": 0, "updated": 0, "resolved": 0, "errors": []}

            # Get unresolved gaps
            unresolved_gaps = ImplementationGap.query.filter(
                ImplementationGap.resolution_status.in_(["open", "in_progress"])
            ).all()

            for gap in unresolved_gaps:
                try:
                    # Check if work package already exists
                    existing_wp = ImplementationWorkPackage.query.filter_by(
                        source_type="gap", source_id=gap.id
                    ).first()

                    if existing_wp:
                        # Update existing work package
                        updated = self._update_gap_work_package(existing_wp, gap)
                        if updated:
                            sync_results["updated"] += 1
                    else:
                        # Create new work package
                        self._create_gap_work_package(gap)
                        sync_results["created"] += 1

                except Exception as e:
                    error_msg = f"Error syncing gap {gap.id}: {str(e)}"
                    logger.error(error_msg)
                    sync_results["errors"].append(error_msg)

            # Mark resolved gaps as completed
            resolved = self._sync_resolved_gaps()
            sync_results["resolved"] = resolved

            logger.info(f"Gap sync completed: {sync_results}")
            return sync_results

        except Exception as e:
            logger.error(f"Error in gap sync: {e}")
            raise

    def sync_vendor_roadmaps(self) -> Dict[str, Any]:
        """
        Synchronize vendor product roadmaps with implementation timeline

        Integrates vendor roadmap data into work packages and deliverables.
        """
        try:
            if not self.sync_batch_id:
                self.start_sync_batch(1, "Vendor roadmap sync")

            sync_results = {"integrations": 0, "updates": 0, "errors": []}

            # Get vendor products with roadmap data
            vendor_products = VendorProduct.query.filter(
                VendorProduct.roadmap_data.isnot(None)
            ).all()

            for product in vendor_products:
                try:
                    # Parse roadmap data
                    roadmap_data = json.loads(product.roadmap_data) if product.roadmap_data else {}

                    # Sync roadmap milestones
                    integrations = self._sync_vendor_roadmap_milestones(product, roadmap_data)
                    sync_results["integrations"] += integrations

                except Exception as e:
                    error_msg = f"Error syncing vendor product {product.id}: {str(e)}"
                    logger.error(error_msg)
                    sync_results["errors"].append(error_msg)

            logger.info(f"Vendor roadmap sync completed: {sync_results}")
            return sync_results

        except Exception as e:
            logger.error(f"Error in vendor roadmap sync: {e}")
            raise

    def sync_work_package_created(self, work_package: ImplementationWorkPackage) -> Dict[str, Any]:
        """Handle work package creation sync"""
        try:
            sync_result = {
                "capability_sync": False,
                "application_sync": False,
                "dependency_sync": False,
            }

            # Sync with capability
            if work_package.source_type == "capability":
                self._sync_capability_to_work_package(work_package)
                sync_result["capability_sync"] = True

            # Sync with applications
            if work_package.source_type == "application":
                self._sync_application_to_work_package(work_package)
                sync_result["application_sync"] = True

            # Create dependency relationships
            self._create_dependency_relationships(work_package)
            sync_result["dependency_sync"] = True

            # Log audit entry
            self._log_audit_entry(
                "work_package",
                work_package.id,
                "create",
                {"source_type": work_package.source_type, "source_id": work_package.source_id},
            )

            return sync_result

        except Exception as e:
            logger.error(f"Error in work package created sync: {e}")
            raise

    def sync_work_package_updated(self, work_package: ImplementationWorkPackage) -> Dict[str, Any]:
        """Handle work package update sync"""
        try:
            sync_result = {
                "capability_sync": False,
                "dependency_sync": False,
                "timeline_sync": False,
            }

            # Update capability relationships
            if work_package.source_type == "capability":
                self._update_capability_relationships(work_package)
                sync_result["capability_sync"] = True

            # Update dependency timeline
            self._update_dependency_timeline(work_package)
            sync_result["dependency_sync"] = True

            # Update timeline calculations
            self._recalculate_work_package_timeline(work_package)
            sync_result["timeline_sync"] = True

            # Log audit entry
            self._log_audit_entry(
                "work_package",
                work_package.id,
                "update",
                {"status": work_package.status, "progress": work_package.progress_percentage},
            )

            return sync_result

        except Exception as e:
            logger.error(f"Error in work package updated sync: {e}")
            raise

    def sync_work_package_deleted(self, work_package_id: int) -> Dict[str, Any]:
        """Handle work package deletion sync"""
        try:
            sync_result = {
                "dependency_cleanup": False,
                "capability_sync": False,
                "audit_logged": False,
            }

            # Clean up dependencies
            self._cleanup_work_package_dependencies(work_package_id)
            sync_result["dependency_cleanup"] = True

            # Update capability sync status
            self._update_capability_sync_status(work_package_id)
            sync_result["capability_sync"] = True

            # Log audit entry
            self._log_audit_entry("work_package", work_package_id, "delete", {})
            sync_result["audit_logged"] = True

            return sync_result

        except Exception as e:
            logger.error(f"Error in work package deleted sync: {e}")
            raise

    def perform_full_sync(self) -> Dict[str, Any]:
        """Perform complete synchronization across all entities"""
        try:
            self.start_sync_batch(1, "Full synchronization")

            full_sync_results = {
                "capabilities": {},
                "applications": {},
                "gaps": {},
                "vendor_roadmaps": {},
                "total_created": 0,
                "total_updated": 0,
                "total_archived": 0,
                "total_errors": 0,
                "sync_duration": 0,
            }

            start_time = datetime.utcnow()

            # Sync capabilities
            full_sync_results["capabilities"] = self.sync_capabilities_to_work_packages()

            # Sync applications
            full_sync_results["applications"] = self.sync_applications_to_deliverables()

            # Sync gaps
            full_sync_results["gaps"] = self.sync_gaps_to_initiatives()

            # Sync vendor roadmaps
            full_sync_results["vendor_roadmaps"] = self.sync_vendor_roadmaps()

            # Calculate totals
            for sync_type in ["capabilities", "applications", "gaps"]:
                results = full_sync_results[sync_type]
                full_sync_results["total_created"] += results.get("created", 0)
                full_sync_results["total_updated"] += results.get("updated", 0)
                full_sync_results["total_archived"] += results.get("archived", 0)
                full_sync_results["total_errors"] += len(results.get("errors", []))

            full_sync_results["sync_duration"] = (datetime.utcnow() - start_time).total_seconds()

            logger.info(f"Full sync completed in {full_sync_results['sync_duration']:.2f}s")
            return full_sync_results

        except Exception as e:
            logger.error(f"Error in full sync: {e}")
            raise

    # Private helper methods
    def _create_capability_work_package(self, capability: UnifiedCapability):
        """Create work package from capability"""
        wp = ImplementationWorkPackage(
            name=f"Implement {capability.name}",
            description=f"Implement {capability.name} capability",
            business_capability=capability.name,
            status="planned",
            priority=self._map_importance_to_priority(capability.strategic_importance),
            source_type="capability",
            source_id=capability.id,
            auto_generated=True,
            confidence_score=0.9,
            generation_method="capability_sync",
            created_by=1,
            last_sync_at=datetime.utcnow(),
            sync_status="synced",
        )

        db.session.add(wp)
        db.session.commit()

        # Create capability relationship
        wp.capabilities.append(capability)
        db.session.commit()

    def _update_capability_work_package(
        self, wp: ImplementationWorkPackage, capability: UnifiedCapability
    ) -> bool:
        """Update work package from capability"""
        updated = False

        if wp.name != f"Implement {capability.name}":
            wp.name = f"Implement {capability.name}"
            updated = True

        if wp.business_capability != capability.name:
            wp.business_capability = capability.name
            updated = True

        new_priority = self._map_importance_to_priority(capability.strategic_importance)
        if wp.priority != new_priority:
            wp.priority = new_priority
            updated = True

        if updated:
            wp.updated_at = datetime.utcnow()
            wp.last_sync_at = datetime.utcnow()
            wp.sync_status = "synced"
            db.session.commit()

        return updated

    def _create_application_deliverable(self, application: ApplicationComponent):
        """Create deliverable from application"""
        wp = ImplementationWorkPackage.query.filter_by(
            source_type="application", source_id=application.id
        ).first()

        if not wp:
            # Create work package first
            wp = ImplementationWorkPackage(
                name=f"Modernize {application.name}",
                description=f"Modernize {application.name} application",
                business_capability=self._get_primary_capability_for_application(application.id),
                status="planned",
                priority="medium",
                source_type="application",
                source_id=application.id,
                auto_generated=True,
                confidence_score=0.8,
                generation_method="application_sync",
                created_by=1,
                last_sync_at=datetime.utcnow(),
                sync_status="synced",
            )
            db.session.add(wp)
            db.session.commit()

        # Create deliverable
        deliverable = Deliverable(
            name=f"{application.name} Modernization",
            description=f"Complete modernization of {application.name}",
            work_package_id=wp.id,
            source_application_id=application.id,
            auto_generated=True,
            generation_method="application_sync",
            created_by=1,
        )

        db.session.add(deliverable)
        db.session.commit()

    def _update_application_deliverable(
        self, deliverable: Deliverable, application: ApplicationComponent
    ) -> bool:
        """Update deliverable from application"""
        updated = False

        if deliverable.name != f"{application.name} Modernization":
            deliverable.name = f"{application.name} Modernization"
            updated = True

        if updated:
            deliverable.updated_at = datetime.utcnow()
            db.session.commit()

        return updated

    def _create_gap_work_package(self, gap: ImplementationGap):
        """Create work package from gap"""
        wp = ImplementationWorkPackage(
            name=f"Resolve {gap.gap_type} Gap: {gap.name}",
            description=f"Address {gap.gap_type}: {gap.description}",
            business_capability=self._infer_capability_from_gap(gap),
            status="planned",
            priority=gap.priority,
            risk_level=gap.risk_level,
            source_type="gap",
            source_id=gap.id,
            auto_generated=True,
            confidence_score=gap.confidence_score,
            generation_method="gap_sync",
            created_by=1,
            last_sync_at=datetime.utcnow(),
            sync_status="synced",
        )

        db.session.add(wp)
        db.session.commit()

    def _update_gap_work_package(
        self, wp: ImplementationWorkPackage, gap: ImplementationGap
    ) -> bool:
        """Update work package from gap"""
        updated = False

        if wp.priority != gap.priority:
            wp.priority = gap.priority
            updated = True

        if wp.risk_level != gap.risk_level:
            wp.risk_level = gap.risk_level
            updated = True

        if updated:
            wp.updated_at = datetime.utcnow()
            wp.last_sync_at = datetime.utcnow()
            wp.sync_status = "synced"
            db.session.commit()

        return updated

    def _archive_orphaned_work_packages(self, source_type: str) -> int:
        """Archive work packages whose source entities no longer exist"""
        archived = 0

        if source_type == "capability":
            # Find work packages for deleted capabilities
            _org_clause, _org_params = org_scope(prefix="wp.")
            orphaned_wps = db.session.execute(
                text(
                    """
                -- ImplementationWorkPackage is an alias for RoadmapWorkPackage
                -- (table roadmap_work_packages), which is where source_type /
                -- source_id live. implementation_work_packages is a different
                -- table and has neither column, so this SELECT raised
                -- UndefinedColumn and nothing was ever archived.
                SELECT wp.id FROM roadmap_work_packages wp
                LEFT JOIN unified_capabilities uc ON wp.source_id = uc.id
                WHERE wp.source_type = 'capability'
                AND wp.source_id IS NOT NULL
                AND uc.id IS NULL
                AND wp.status != 'archived'
            """
                    + _org_clause
                ),
                _org_params,
            ).fetchall()

            for wp_id in orphaned_wps:
                wp = ImplementationWorkPackage.query.get(wp_id[0])
                wp.status = "archived"
                wp.updated_at = datetime.utcnow()
                archived += 1

            db.session.commit()

        return archived

    def _archive_orphaned_deliverables(self) -> int:
        """Archive deliverables for deleted applications"""
        archived = 0

        orphaned_deliverables = db.session.execute(  # tenant-filtered: scoped via parent FK (source_application_id → applications)
            text(
                """
            SELECT d.id FROM planning_deliverables d
            LEFT JOIN applications a ON d.source_application_id = a.id
            WHERE d.source_application_id IS NOT NULL
            AND a.id IS NULL
            AND d.status != 'archived'
        """
            )
        ).fetchall()

        for deliverable_id in orphaned_deliverables:
            deliverable = Deliverable.query.get(deliverable_id[0])
            deliverable.status = "archived"
            deliverable.updated_at = datetime.utcnow()
            archived += 1

        db.session.commit()

        return archived

    def _sync_resolved_gaps(self) -> int:
        """Mark work packages as completed when gaps are resolved"""
        resolved = 0

        resolved_gaps = ImplementationGap.query.filter(ImplementationGap.resolution_status == "resolved").all()

        for gap in resolved_gaps:
            wp = ImplementationWorkPackage.query.filter_by(
                source_type="gap", source_id=gap.id
            ).first()

            if wp and wp.status != "completed":
                wp.status = "completed"
                wp.progress_percentage = 100
                wp.updated_at = datetime.utcnow()
                resolved += 1

        db.session.commit()

        return resolved

    def _sync_vendor_roadmap_milestones(self, product: VendorProduct, roadmap_data: Dict) -> int:
        """Sync vendor roadmap milestones with work packages"""
        integrations = 0

        milestones = roadmap_data.get("milestones", [])

        for milestone in milestones:
            # Check if work package already exists for this milestone
            existing_wp = ImplementationWorkPackage.query.filter_by(
                name=f"Vendor Milestone: {milestone.get('name', 'Unknown')}"
            ).first()

            if not existing_wp:
                # Create work package for vendor milestone
                wp = ImplementationWorkPackage(
                    name=f"Vendor Milestone: {milestone.get('name', 'Unknown')}",
                    description=f"Vendor {product.name} milestone: {milestone.get('description', '')}",
                    business_capability="vendor_management",
                    status="planned",
                    priority="medium",
                    source_type="vendor",
                    source_id=product.id,
                    auto_generated=True,
                    confidence_score=0.7,
                    generation_method="vendor_roadmap_sync",
                    created_by=1,
                    automation_metadata=json.dumps(
                        {"vendor_product_id": product.id, "milestone_data": milestone}
                    ),
                )

                db.session.add(wp)
                integrations += 1

        db.session.commit()

        return integrations

    def _sync_capability_to_work_package(self, work_package: ImplementationWorkPackage):
        """Sync capability relationship to work package"""
        if work_package.source_id:
            capability = UnifiedCapability.query.get(work_package.source_id)
            if capability and capability not in work_package.capabilities:
                work_package.capabilities.append(capability)
                db.session.commit()

    def _sync_application_to_work_package(self, work_package: ImplementationWorkPackage):
        """Sync application relationship to work package"""
        if work_package.source_id:
            application = ApplicationComponent.query.get(work_package.source_id)
            if application:
                # Create deliverable if needed
                existing_deliverable = Deliverable.query.filter_by(
                    work_package_id=work_package.id, source_application_id=application.id
                ).first()

                if not existing_deliverable:
                    deliverable = Deliverable(
                        name=f"{application.name} Implementation",
                        description=f"Implementation of {application.name}",
                        work_package_id=work_package.id,
                        source_application_id=application.id,
                        auto_generated=True,
                        generation_method="sync",
                    )
                    db.session.add(deliverable)
                    db.session.commit()

    def _create_dependency_relationships(self, work_package: ImplementationWorkPackage):
        """Create automatic dependency relationships"""
        # Add dependencies based on business logic
        if work_package.source_type == "capability":
            # Capability work packages should depend on assessment work packages
            assessment_wp = ImplementationWorkPackage.query.filter_by(
                name="Portfolio Assessment"
            ).first()

            if assessment_wp and assessment_wp not in work_package.dependencies:
                work_package.dependencies.append(assessment_wp)
                db.session.commit()

    def _update_capability_relationships(self, work_package: ImplementationWorkPackage):
        """Update capability relationships when work package changes"""
        # Update related work packages when capability work package changes
        if work_package.source_type == "capability":
            dependent_wps = (
                ImplementationWorkPackage.query.filter(
                    ImplementationWorkPackage.source_type == "application"
                )
                .join(
                    work_package_capabilities,
                    ImplementationWorkPackage.id == work_package_capabilities.c.work_package_id,
                )
                .filter(work_package_capabilities.c.capability_id == work_package.source_id)
                .all()
            )

            for dependent_wp in dependent_wps:
                # Update dependent work package timeline
                if work_package.end_date and dependent_wp.start_date:
                    if dependent_wp.start_date < work_package.end_date:
                        dependent_wp.start_date = work_package.end_date + timedelta(days=1)
                        dependent_wp.updated_at = datetime.utcnow()

            db.session.commit()

    def _update_dependency_timeline(self, work_package: ImplementationWorkPackage):
        """Update dependent work packages when timeline changes"""
        # Update all dependent work packages
        for dependent in work_package.dependents:
            if work_package.end_date and dependent.start_date:
                if dependent.start_date <= work_package.end_date:
                    # Move dependent work package start date
                    dependent.start_date = work_package.end_date + timedelta(days=1)

                    # Also adjust end date if duration is fixed
                    if dependent.duration_days:
                        dependent.end_date = dependent.start_date + timedelta(
                            days=dependent.duration_days - 1
                        )

                    dependent.updated_at = datetime.utcnow()

        db.session.commit()

    def _recalculate_work_package_timeline(self, work_package: ImplementationWorkPackage):
        """Recalculate timeline metrics for work package"""
        if work_package.start_date and work_package.end_date:
            work_package.duration_days = (work_package.end_date - work_package.start_date).days + 1
            work_package.updated_at = datetime.utcnow()
            db.session.commit()

    def _cleanup_work_package_dependencies(self, work_package_id: int):
        """Clean up dependencies when work package is deleted"""
        # Remove from all dependent work packages
        db.session.execute(  # tenant-filtered: scoped via parent FK (work_package_id)
            text(
                """
            DELETE FROM work_package_dependencies
            WHERE work_package_id = :wp_id OR dependency_id = :wp_id
        """
            ),
            {"wp_id": work_package_id},
        )

        db.session.commit()

    def _update_capability_sync_status(self, work_package_id: int):
        """Update capability sync status when work package is deleted"""
        # No-op by schema: unified_capabilities has no last_sync_at or
        # sync_status column (sync state lives on roadmap_work_packages), so the
        # UPDATE that used to be here raised UndefinedColumn on every call and
        # marked nothing. There is no capability-level sync flag to set, and
        # inventing one would mean adding a column, so the dead statement is
        # removed rather than retargeted.
        logger.debug(
            "No capability sync flag to clear for work package %s: "
            "unified_capabilities has no sync-state columns",
            work_package_id,
        )

    def _log_audit_entry(self, entity_type: str, entity_id: int, action: str, details: Dict):
        """Log audit entry for synchronization"""
        audit = RoadmapAudit(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            new_values=json.dumps(details),
            batch_id=self.sync_batch_id,
            user_id=1,
            timestamp=datetime.utcnow(),
        )

        db.session.add(audit)
        db.session.commit()

    # Utility methods
    def _map_importance_to_priority(self, importance: str) -> str:
        """Map strategic importance to work package priority"""
        mapping = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
        return mapping.get(importance, "medium")

    def _infer_capability_from_gap(self, gap: ImplementationGap) -> str:
        """Infer capability from gap"""
        if gap.source_capability_id:
            capability = UnifiedCapability.query.get(gap.source_capability_id)
            return capability.name if capability else "unknown"

        # Default mapping based on gap type
        type_mapping = {
            "technology": "technology_management",
            "process": "process_optimization",
            "skill": "human_resources",
            "resource": "resource_management",
        }

        return type_mapping.get(gap.gap_type, "general_improvement")

    def _get_primary_capability_for_application(self, application_id: int) -> str:
        """Get primary capability for application"""
        result = db.session.execute(  # tenant-filtered: scoped via parent FK (application_id)
            text(
                """
            -- application_capability_mapping names its capability FK
            -- business_capability_id; there is no capability_id column on that
            -- table, so this join raised UndefinedColumn.
            SELECT uc.name FROM unified_capabilities uc
            JOIN application_capability_mapping acm ON uc.id = acm.business_capability_id
            WHERE acm.application_id = :app_id
            LIMIT 1
        """
            ),
            {"app_id": application_id},
        ).fetchone()

        return result[0] if result else "unknown_capability"

    def _application_needs_deliverable(self, application: ApplicationComponent) -> bool:
        """Determine if application needs a deliverable"""
        # Check if application is critical or needs modernization
        if hasattr(application, "criticality"):
            return application.criticality in ["critical", "high"]

        # Check if application is old (older than 5 years)
        if hasattr(application, "created_date"):
            five_years_ago = datetime.utcnow() - timedelta(days=365 * 5)
            return application.created_date < five_years_ago

        # Default to creating deliverable
        return True
