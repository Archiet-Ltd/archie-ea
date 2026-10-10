"""
-> app.modules.import_batch.services.batch_service

Batch Approval Service

Handles review and approval of batch import elements.
Manages committing approved elements to the database.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.modules.import_batch.v2.services.unified_import.duplicate_detector_v2 import (
    DuplicateDetector,
)
from app.models.batch_import import (  # dead-code-ok
    AppProcessingStatus,
    BatchImportApplication,
    BatchImportBatch,
    BatchImportCheckpoint,
    BatchImportElement,
    BatchStatus,
    CheckpointType,
    ElementApprovalStatus,
)
from app.services.application_cost_accessor import (
    apply_cost_to_application,
    detect_cost_columns_from_dict,
    get_reporting_currency,
    map_import_cost_columns,
)

logger = logging.getLogger(__name__)


class BatchApprovalService:
    """
    Service for reviewing and committing batches.

    Handles:
    - Batch approval/rejection
    - Individual element approval/rejection/modification
    - Committing approved elements to database
    """

    def approve_batch(
        self,
        batch_id: int,
        user_id: int,
        notes: str = None,
        auto_commit: bool = True,
    ) -> BatchImportBatch:
        """
        Approve all pending elements in a batch.

        Args:
            batch_id: ID of the batch to approve
            user_id: ID of the approving user
            notes: Optional review notes
            auto_commit: Whether to immediately commit after approval

        Returns:
            Updated batch instance
        """
        batch = BatchImportBatch.query.get_or_404(batch_id)

        if batch.status not in [BatchStatus.READY_FOR_REVIEW, BatchStatus.APPROVED]:
            raise ValueError(f"Batch cannot be approved in status: {batch.status.value}")

        # Approve all pending elements
        pending_elements = BatchImportElement.query.filter(
            BatchImportElement.batch_id == batch_id,
            BatchImportElement.approval_status == ElementApprovalStatus.PENDING,
        ).all()

        approved_count = 0
        for element in pending_elements:
            element.approval_status = ElementApprovalStatus.APPROVED
            element.approved_by_id = user_id
            element.approved_at = datetime.utcnow()
            approved_count += 1

        # Update batch
        batch.status = BatchStatus.APPROVED
        batch.reviewed_by_id = user_id
        batch.reviewed_at = datetime.utcnow()
        batch.review_notes = notes
        batch.elements_approved = BatchImportElement.query.filter(
            BatchImportElement.batch_id == batch_id,
            BatchImportElement.approval_status == ElementApprovalStatus.APPROVED,
        ).count()

        db.session.commit()

        logger.info(f"Approved batch {batch_id}: {approved_count} elements")

        # Auto-commit if requested
        if auto_commit:
            return self.commit_batch(batch_id, user_id)

        return batch

    def reject_batch(
        self,
        batch_id: int,
        user_id: int,
        reason: str,
    ) -> BatchImportBatch:
        """
        Reject a batch.

        Args:
            batch_id: ID of the batch to reject
            user_id: ID of the rejecting user
            reason: Reason for rejection

        Returns:
            Updated batch instance
        """
        batch = BatchImportBatch.query.get_or_404(batch_id)

        if batch.status not in [BatchStatus.READY_FOR_REVIEW, BatchStatus.APPROVED]:
            raise ValueError(f"Batch cannot be rejected in status: {batch.status.value}")

        # Reject all pending elements
        BatchImportElement.query.filter(
            BatchImportElement.batch_id == batch_id,
            BatchImportElement.approval_status.in_(
                [
                    ElementApprovalStatus.PENDING,
                    ElementApprovalStatus.APPROVED,
                ]
            ),
        ).update(
            {
                "approval_status": ElementApprovalStatus.REJECTED,
                "rejection_reason": reason,
            },
            synchronize_session=False,
        )

        # Update batch
        batch.status = BatchStatus.REJECTED
        batch.reviewed_by_id = user_id
        batch.reviewed_at = datetime.utcnow()
        batch.review_notes = reason
        batch.elements_rejected = BatchImportElement.query.filter_by(batch_id=batch_id).count()

        db.session.commit()

        logger.info(f"Rejected batch {batch_id}: {reason}")

        # Check if job is complete
        from app.modules.import_batch.v2.services.batch_import_service_v2 import BatchImportService

        BatchImportService().check_and_complete_job(batch.job_id)

        return batch

    def approve_element(
        self,
        element_id: int,
        user_id: int,
    ) -> BatchImportElement:
        """Approve a single element."""
        element = BatchImportElement.query.get_or_404(element_id)

        if element.approval_status not in [
            ElementApprovalStatus.PENDING,
            ElementApprovalStatus.REJECTED,
        ]:
            raise ValueError(
                f"Element cannot be approved in status: {element.approval_status.value}"
            )

        element.approval_status = ElementApprovalStatus.APPROVED
        element.approved_by_id = user_id
        element.approved_at = datetime.utcnow()
        element.rejection_reason = None

        # Update batch counts
        element.batch.elements_approved = BatchImportElement.query.filter(
            BatchImportElement.batch_id == element.batch_id,
            BatchImportElement.approval_status == ElementApprovalStatus.APPROVED,
        ).count()

        db.session.commit()

        return element

    def reject_element(
        self,
        element_id: int,
        user_id: int,
        reason: str,
    ) -> BatchImportElement:
        """Reject a single element."""
        element = BatchImportElement.query.get_or_404(element_id)

        if element.approval_status == ElementApprovalStatus.REJECTED:
            raise ValueError("Element is already rejected")

        element.approval_status = ElementApprovalStatus.REJECTED
        element.rejection_reason = reason

        # Update batch counts
        element.batch.elements_rejected = BatchImportElement.query.filter(
            BatchImportElement.batch_id == element.batch_id,
            BatchImportElement.approval_status == ElementApprovalStatus.REJECTED,
        ).count()

        db.session.commit()

        return element

    def modify_element(
        self,
        element_id: int,
        user_id: int,
        new_data: Dict[str, Any],
    ) -> BatchImportElement:
        """
        Modify an element before approval.

        Preserves original data for audit purposes.
        """
        element = BatchImportElement.query.get_or_404(element_id)

        # Preserve original if not already done
        if not element.is_modified:
            element.original_data = element.element_data

        # Apply modifications
        element.modified_data = new_data
        element.is_modified = True
        element.element_name = new_data.get("name", element.element_name)
        element.element_description = new_data.get("description", element.element_description)

        # Mark as modified (special approval status)
        element.approval_status = ElementApprovalStatus.MODIFIED
        element.approved_by_id = user_id
        element.approved_at = datetime.utcnow()

        db.session.commit()

        return element

    def commit_batch(self, batch_id: int, user_id: int) -> BatchImportBatch:
        """
        Commit approved elements to the database.

        Creates actual records in target tables with proper transaction management.
        Uses overall transaction with savepoints to ensure atomicity.

        Args:
            batch_id: ID of the batch to commit
            user_id: ID of the user committing

        Returns:
            Updated batch instance

        Raises:
            ValueError: If batch cannot be committed
            SQLAlchemyError: If database transaction fails
        """
        batch = BatchImportBatch.query.get_or_404(batch_id)

        if batch.status not in [BatchStatus.APPROVED]:
            raise ValueError(f"Batch cannot be committed in status: {batch.status.value}")

        # Get all approved elements (including modified)
        elements = BatchImportElement.query.filter(
            BatchImportElement.batch_id == batch_id,
            BatchImportElement.approval_status.in_(
                [
                    ElementApprovalStatus.APPROVED,
                    ElementApprovalStatus.MODIFIED,
                ]
            ),
            BatchImportElement.is_committed == False,
        ).all()

        committed_count = 0
        errors = []
        successful_applications = []
        successful_elements = []

        # Load conflict resolutions from job (if any)
        conflict_resolutions = {}
        if batch.job and batch.job.custom_field_mappings:
            try:
                mappings = batch.job.custom_field_mappings
                resolutions = mappings.get("conflict_resolutions", [])
                for r in resolutions:
                    row = r.get("row")
                    if row is not None:
                        conflict_resolutions[row] = r
            except (AttributeError, TypeError, KeyError) as e:
                logger.warning(f"Failed to parse conflict resolutions: {e}")

        # Start overall transaction
        try:
            # Create main transaction savepoint
            main_savepoint = db.session.begin_nested()
            
            # First, commit applications
            applications = BatchImportApplication.query.filter(
                BatchImportApplication.batch_id == batch_id,
                BatchImportApplication.status == AppProcessingStatus.COMPLETED,
            ).order_by(BatchImportApplication.row_number).all()
            
            app_lookup = DuplicateDetector.preload_existing_apps()
            skipped_by_resolution = 0
            
            for app in applications:
                app_savepoint = None
                try:
                    # Create savepoint for each application
                    app_savepoint = db.session.begin_nested()
                    
                    committed_app = self._commit_application(app, conflict_resolutions, app_lookup)
                    if committed_app:
                        app.committed_application_id = committed_app.id
                        app.status = AppProcessingStatus.COMMITTED
                        successful_applications.append(app.id)
                    elif committed_app is None and app.row_number in conflict_resolutions:
                        skipped_by_resolution += 1
                        app.status = AppProcessingStatus.COMMITTED
                        successful_applications.append(app.id)
                    
                    # Release application savepoint
                    app_savepoint.commit()
                    
                except Exception as e:
                    # Rollback only this application's savepoint
                    if app_savepoint is not None:
                        app_savepoint.rollback()
                    logger.error(f"Error committing application {app.id}: {e}")
                    errors.append(f"Application {app.application_name}: processing failed")
                    # Continue with next application instead of failing entire batch

            # Flush application changes before processing elements
            db.session.flush()

            # PROG-002: attach committed applications to a Transformation
            # Programme's current-state baseline solution when the job was
            # created with a programme target (custom_field_mappings carries
            # programme_initiative_id — no schema change).
            try:
                self._attach_to_programme(batch, applications, user_id)
            except Exception as e:
                logger.error(f"Programme attachment failed (apps still committed): {e}")
                errors.append("Programme attachment failed — link solutions manually")

            # Then commit elements (with reference to committed applications)
            for element in elements:
                element_savepoint = None
                try:
                    # Create savepoint for each element
                    element_savepoint = db.session.begin_nested()
                    
                    committed_id, committed_table = self._commit_element(element)
                    if committed_id:
                        element.is_committed = True
                        element.committed_at = datetime.utcnow()
                        element.committed_record_id = committed_id
                        element.committed_table = committed_table
                        committed_count += 1
                        successful_elements.append(element.id)
                    
                    # Release element savepoint
                    element_savepoint.commit()
                    
                except Exception as e:
                    # Rollback only this element's savepoint
                    if element_savepoint is not None:
                        element_savepoint.rollback()
                    logger.error(f"Error committing element {element.id}: {e}")
                    errors.append(f"Element {element.element_name}: processing failed")
                    # Continue with next element instead of failing entire batch

            # Check failure threshold - rollback entire batch if too many failures
            total_elements = len(elements)
            failure_rate = len(errors) / total_elements if total_elements > 0 else 0
            failure_threshold = current_app.config.get("BATCH_COMMIT_FAILURE_THRESHOLD", 0.5)  # 50% default
            
            if failure_rate > failure_threshold:
                # Too many failures - rollback entire batch
                main_savepoint.rollback()
                batch.status = BatchStatus.FAILED
                batch.error_message = f"Batch commit failed: {len(errors)}/{total_elements} elements failed ({failure_rate:.1%} > {failure_threshold:.1%} threshold)"
                batch.committed_at = datetime.utcnow()
                db.session.commit()
                
                logger.error(f"Batch {batch_id} rolled back due to high failure rate: {len(errors)}/{total_elements}")
                raise ValueError(f"Batch commit failed: failure rate {failure_rate:.1%} exceeds threshold {failure_threshold:.1%}")
            
            # Update batch status
            batch.status = BatchStatus.COMMITTED
            batch.committed_at = datetime.utcnow()

            # Create checkpoint
            checkpoint = BatchImportCheckpoint(
                batch_id=batch_id,
                checkpoint_type=CheckpointType.BATCH_COMMITTED,
                checkpoint_data={
                    "committed_count": committed_count,
                    "skipped_by_resolution": skipped_by_resolution,
                    "error_count": len(errors),
                    "errors": errors[:10],  # Limit stored errors
                    "successful_applications": len(successful_applications),
                    "successful_elements": len(successful_elements),
                },
                elements_staged=committed_count,
            )
            db.session.add(checkpoint)

            # Commit the entire transaction
            db.session.commit()
            
            logger.info(f"Committed batch {batch_id}: {committed_count} elements, {len(errors)} errors")
            
            # Check if job is complete
            from app.modules.import_batch.v2.services.batch_import_service_v2 import BatchImportService
            batch_service = BatchImportService()
            batch_service.check_and_complete_job(batch.job_id)

            return batch

        except Exception as e:
            # Rollback the entire transaction on critical failure
            try:
                db.session.rollback()
                logger.error(f"Critical error in batch {batch_id} commit, rolled back: {e}")
                
                # Update batch status to failed
                batch.status = BatchStatus.FAILED
                batch.error_message = f"Commit failed: {str(e)}"
                db.session.commit()
                
            except Exception as rollback_error:
                logger.error(f"Failed to rollback batch {batch_id}: {rollback_error}")
            
            raise SQLAlchemyError(f"Batch commit failed: {e}") from e

    def commit_application(
        self,
        application_id: int,
        user_id: int,
        auto_commit: bool = True,
    ) -> BatchImportApplication:
        """
        Commit a single application's approved elements to the database.

        Args:
            application_id: ID of the BatchImportApplication to commit
            user_id: ID of the user performing the commit
            auto_commit: If True, commit approved elements after committing the application record
        Returns:
            Updated BatchImportApplication instance
        """
        app = BatchImportApplication.query.get_or_404(application_id)

        if app.status not in [AppProcessingStatus.COMPLETED, AppProcessingStatus.COMMITTED]:
            raise ValueError(f"Application cannot be committed in status: {app.status.value}")

        # If already committed, return
        if getattr(app, "committed_application_id", None):
            logger.info(f"Application {application_id} already committed")
            return app

        committed_count = 0
        errors = []

        try:
            # Commit application record if not present
            committed_app = self._commit_application(app)
            if committed_app:
                app.committed_application_id = committed_app.id
                app.status = AppProcessingStatus.COMMITTED
                db.session.flush()
        except Exception as e:
            logger.error(f"Error committing application {app.id}: {e}")
            raise

        if auto_commit:
            # Commit approved elements for this application
            elements = BatchImportElement.query.filter(
                BatchImportElement.application_id == application_id,
                BatchImportElement.approval_status.in_(
                    [
                        ElementApprovalStatus.APPROVED,
                        ElementApprovalStatus.MODIFIED,
                    ]
                ),
                BatchImportElement.is_committed == False,
            ).all()

            for element in elements:
                try:
                    committed_id, committed_table = self._commit_element(element)
                    if committed_id:
                        element.is_committed = True
                        element.committed_at = datetime.utcnow()
                        element.committed_record_id = committed_id
                        element.committed_table = committed_table
                        committed_count += 1
                except Exception as e:
                    logger.error(f"Error committing element {element.id}: {e}")
                    errors.append(f"Element {element.element_name}: {str(e)}")

            db.session.commit()

        # Update batch counts
        batch = BatchImportBatch.query.get(app.batch_id)
        batch.elements_approved = BatchImportElement.query.filter(
            BatchImportElement.batch_id == batch.id,
            BatchImportElement.approval_status == ElementApprovalStatus.APPROVED,
        ).count()
        batch.elements_rejected = BatchImportElement.query.filter(
            BatchImportElement.batch_id == batch.id,
            BatchImportElement.approval_status == ElementApprovalStatus.REJECTED,
        ).count()
        db.session.commit()

        # Create a checkpoint for this commit
        checkpoint = BatchImportCheckpoint(
            batch_id=app.batch_id,
            checkpoint_type=CheckpointType.BATCH_COMMITTED,
            checkpoint_data={
                "application_id": app.id,
                "committed_elements": committed_count,
                "errors": errors[:10],
            },
            elements_staged=committed_count,
        )
        db.session.add(checkpoint)
        db.session.commit()

        # Check if job is complete
        from app.modules.import_batch.v2.services.batch_import_service_v2 import BatchImportService

        BatchImportService().check_and_complete_job(batch.job_id)

        logger.info(
            f"Committed application {application_id}: {committed_count} elements, {len(errors)} errors"
        )

        return app

    def _commit_application(
        self,
        app: BatchImportApplication,
        conflict_resolutions: Optional[Dict] = None,
        app_lookup: Optional[Dict] = None,
    ) -> Optional[ApplicationComponent]:
        """
        Commit an application to the ApplicationComponent table.

        Respects user conflict resolution decisions when available.
        Returns the ApplicationComponent, or None if skipped.
        """
        # Check if user has a conflict resolution for this row
        resolution = (conflict_resolutions or {}).get(app.row_number)
        if resolution:
            action = resolution.get("action", "create_new")
            target_id = resolution.get("target_app_id")

            if action == "skip":
                logger.info(
                    "Skipping application %s (row %d) — user resolution: skip",
                    app.application_name, app.row_number,
                )
                return None

            if action == "update" and target_id:
                # Merge into existing application
                existing = ApplicationComponent.query.get(target_id)
                if existing:
                    logger.info(
                        "Merging application %s (row %d) into existing ID %s — user resolution: update",
                        app.application_name, app.row_number, target_id,
                    )
                    return self._merge_into_existing(app, existing)
                else:
                    logger.warning(
                        "Target app %s not found for merge, creating new instead", target_id
                    )

            # action == "create_new" falls through to normal creation below

        # Check for existing application with same name (case-insensitive, shared detector)
        if app_lookup:
            match = DuplicateDetector.find_existing_app(app.application_name, app_lookup)
        else:
            # Fallback if no preloaded lookup provided
            match = None
            detector = DuplicateDetector()
            existing_id = detector.check_single_name(app.application_name)
            if existing_id:
                match = {"id": existing_id, "name": app.application_name}
        if match:
            existing = ApplicationComponent.query.get(match["id"])
            if existing:
                logger.debug("Application %s already exists (id=%s), returning existing",
                             app.application_name, match["id"])
                return existing

        # Create new application
        # NOTE: ApplicationComponent has no `status` column — the old
        # status="Active" kwarg made every create-path commit fail (masked
        # until the batch state machine was fixed). Lifecycle comes from
        # source_data below.
        new_app = ApplicationComponent(
            name=app.application_name,
            description=app.application_description,
            component_type=app.application_type or "Application",
        )

        # Add vendor if available
        if app.vendor_name:
            # vendor_name is the real column; `vendor` is not mapped on
            # ApplicationComponent (the old assignment vanished silently)
            new_app.vendor_name = app.vendor_name

        # Copy additional fields from source data
        source = app.source_data or {}
        if "lifecycle_status" in source:
            from app.config.abacus_field_mapping import normalize_lifecycle_status

            new_app.lifecycle_status = normalize_lifecycle_status(source["lifecycle_status"])
        if "criticality" in source:
            new_app.criticality = source["criticality"]
        if "business_domain" in source:
            new_app.business_domain = source["business_domain"]

        # Apply cost fields through the accessor
        cost_mapping = self._extract_cost_mapping(source)
        if cost_mapping:
            parsed = map_import_cost_columns(source, cost_mapping, reporting_currency=get_reporting_currency())
            apply_cost_to_application(new_app, parsed["cost_fields"])

        db.session.add(new_app)
        db.session.flush()

        return new_app

    def _attach_to_programme(self, batch, applications, user_id) -> None:
        """PROG-002: link committed apps into the target programme's
        Current-State Baseline solution (shared helper — same path the
        Salesforce discovery connector uses)."""
        mappings = (batch.job.custom_field_mappings or {}) if batch.job else {}
        initiative_id = mappings.get("programme_initiative_id")
        if not initiative_id:
            return

        from app.modules.solutions_strategic.v2.services.programme_governance_service import (
            ProgrammeGovernanceService,
        )

        committed_ids = [
            a.committed_application_id for a in applications if a.committed_application_id
        ]
        ProgrammeGovernanceService.link_apps_to_baseline(
            int(initiative_id), committed_ids, user_id
        )
        # PROG-005: every landscape import captures a governance snapshot —
        # the diff vs the previous one is the drift signal (ARB-escalated).
        try:
            ProgrammeGovernanceService.snapshot_programme(
                int(initiative_id), user_id, source="landscape_import",
                ai_review=True,  # PROG-013: AI on contact — review the landed estate
            )
        except Exception as exc:
            logger.error("Programme snapshot failed (import unaffected): %s", exc)

    def _extract_cost_mapping(self, source: Dict[str, Any]) -> Dict[str, str]:
        """
        Build a cost column mapping from the available source data keys.

        Delegates to the shared detect_cost_columns_from_dict in the accessor module.
        """
        return detect_cost_columns_from_dict(source)

    def _merge_into_existing(
        self,
        app: BatchImportApplication,
        existing: ApplicationComponent,
    ) -> ApplicationComponent:
        """Merge import application data into an existing ApplicationComponent.
        
        Preserves existing data when import fields are empty or falsy.
        Only overwrites with valid, non-empty data.
        """
        # Handle description field
        if app.application_description:
            # Only overwrite if existing is empty/null, or if import has meaningful content
            if not existing.description or (app.application_description.strip() and 
                                         app.application_description != existing.description):
                existing.description = app.application_description
        
        # Handle vendor field  
        if app.vendor_name:
            # Only overwrite if existing is empty/null, or if import has meaningful content
            if not existing.vendor or (app.vendor_name.strip() and 
                                     app.vendor_name != existing.vendor):
                existing.vendor = app.vendor_name
        
        # Handle source data fields with comprehensive empty string checking
        source = app.source_data or {}
        
        # Define all mergeable fields from source data
        mergeable_fields = [
            "lifecycle_status", "criticality", "business_domain",
            "business_owner", "technical_owner", "support_team",
            "environment", "location", "cost_center", "project_code"
        ]
        
        for field in mergeable_fields:
            import_val = source.get(field)
            
            # Skip if import value is empty, None, or only whitespace
            if import_val is None:
                continue
            if isinstance(import_val, str) and not import_val.strip():
                continue
                
            # Get existing value
            existing_val = getattr(existing, field, None)  # model-safety-ok
            
            # Only overwrite if:
            # 1. Existing is empty/null, OR
            # 2. Import has meaningful content different from existing
            if (not existing_val or 
                (isinstance(import_val, str) and import_val.strip() and import_val != existing_val)):
                setattr(existing, field, import_val)
        
        # Apply cost fields through the accessor
        cost_mapping = self._extract_cost_mapping(source)
        if cost_mapping:
            parsed = map_import_cost_columns(source, cost_mapping, reporting_currency=get_reporting_currency())
            apply_cost_to_application(existing, parsed["cost_fields"])
        
        return existing

    def _commit_element(self, element: BatchImportElement) -> Tuple[Optional[int], Optional[str]]:
        """
        Commit a single element to its target table.

        Returns tuple of (committed_id, table_name) or (None, None) if failed.
        """
        element_data = element.modified_data if element.is_modified else element.element_data

        # Route to appropriate commit handler based on element type
        if element.element_type == "archimate_element":
            return self._commit_archimate_element(element, element_data)
        elif element.element_type == "capability_mapping":
            return self._commit_capability_mapping(element, element_data)
        else:
            logger.warning(f"Unknown element type: {element.element_type}")
            return None, None

    def _commit_archimate_element(
        self,
        element: BatchImportElement,
        data: Dict[str, Any],
    ) -> Tuple[Optional[int], Optional[str]]:
        """Commit an ArchiMate element to the archimate_element table."""
        try:
            from app.models.archimate_core import ArchimateElement

            # Check if element already exists
            existing = ArchimateElement.query.filter_by(name=element.element_name).first()
            if existing:
                return existing.id, "archimate_element"

            # Create new element
            archimate_elem = ArchimateElement(
                name=element.element_name,
                description=element.element_description,
                element_type=element.element_subtype or data.get("type", "element"),
                layer=element.archimate_layer,
                properties=data.get("properties", {}),
            )

            db.session.add(archimate_elem)
            db.session.flush()

            return archimate_elem.id, "archimate_element"

        except Exception as e:
            logger.error(f"Error committing ArchiMate element: {e}")
            # Store in a generic staging table as fallback
            return None, None

    def _commit_capability_mapping(
        self,
        element: BatchImportElement,
        data: Dict[str, Any],
    ) -> Tuple[Optional[int], Optional[str]]:
        """Commit a capability mapping."""
        # Implementation depends on capability model
        # For now, return None to indicate not implemented
        return None, None

    def get_batch_elements(
        self,
        batch_id: int,
        status_filter: Optional[str] = None,
        layer_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[BatchImportElement], int]:
        """
        Get elements for a batch with optional filtering.

        Returns tuple of (elements, total_count).
        """
        query = BatchImportElement.query.filter_by(batch_id=batch_id)

        if status_filter:
            try:
                status_enum = ElementApprovalStatus(status_filter)
                query = query.filter_by(approval_status=status_enum)
            except ValueError:
                logger.exception("Failed to parse status filter")
                pass

        if layer_filter:
            query = query.filter_by(archimate_layer=layer_filter)

        total = query.count()
        elements = (
            query.order_by(
                BatchImportElement.archimate_layer,
                BatchImportElement.element_type,
                BatchImportElement.element_name,
            )
            .offset(offset)
            .limit(limit)
            .all()
        )

        return elements, total

    def get_batch_summary(self, batch_id: int) -> Dict[str, Any]:
        """Get a summary of elements in a batch grouped by layer and type."""
        batch = BatchImportBatch.query.get_or_404(batch_id)

        # Group elements by layer
        layer_summary = {}
        for element in batch.elements:
            layer = element.archimate_layer or "other"
            if layer not in layer_summary:
                layer_summary[layer] = {
                    "total": 0,
                    "approved": 0,
                    "rejected": 0,
                    "pending": 0,
                    "types": {},
                }

            layer_summary[layer]["total"] += 1

            if element.approval_status == ElementApprovalStatus.APPROVED:
                layer_summary[layer]["approved"] += 1
            elif element.approval_status == ElementApprovalStatus.REJECTED:
                layer_summary[layer]["rejected"] += 1
            else:
                layer_summary[layer]["pending"] += 1

            # Track by type
            elem_type = element.element_subtype or element.element_type
            if elem_type not in layer_summary[layer]["types"]:
                layer_summary[layer]["types"][elem_type] = 0
            layer_summary[layer]["types"][elem_type] += 1

        # Group elements by application
        app_summary = {}
        for app in batch.applications:
            app_summary[app.id] = {
                "name": app.application_name,
                "status": app.status.value if app.status else "unknown",
                "elements_count": app.elements_generated,
                "confidence": app.average_confidence_score,
            }

        return {
            "batch": batch.to_dict(),
            "by_layer": layer_summary,
            "by_application": app_summary,
            "totals": {
                "elements": batch.total_elements_generated,
                "approved": batch.elements_approved,
                "rejected": batch.elements_rejected,
                "pending": batch.total_elements_generated
                - batch.elements_approved
                - batch.elements_rejected,
            },
        }

    def approve_high_confidence_elements(
        self,
        batch_id: int,
        user_id: int,
        threshold: float = 0.85,
    ) -> int:
        """
        Auto-approve elements above a confidence threshold.

        Returns count of approved elements.
        """
        elements = BatchImportElement.query.filter(
            BatchImportElement.batch_id == batch_id,
            BatchImportElement.approval_status == ElementApprovalStatus.PENDING,
            BatchImportElement.confidence_score >= threshold,
        ).all()

        for element in elements:
            element.approval_status = ElementApprovalStatus.APPROVED
            element.approved_by_id = user_id
            element.approved_at = datetime.utcnow()

        db.session.commit()

        logger.info(
            f"Auto-approved {len(elements)} elements in batch {batch_id} with confidence >= {threshold}"
        )

        return len(elements)
