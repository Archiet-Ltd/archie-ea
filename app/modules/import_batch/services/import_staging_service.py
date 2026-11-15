"""
-> app.modules.import_batch.services.import_service

Import Staging Service - Transactional Import with Checkpointing

Manages import sessions with atomic transactions and checkpoint-based recovery.
Prevents data loss from LLM API failures, credit exhaustion, or server interruptions.

Key Features:
- Atomic transactions for all import operations
- Checkpoint markers after each successful step
- Staging area for generated elements before commit
- Recovery from last successful checkpoint
- Progress tracking and cost estimation
- Comprehensive error handling with retry logic
"""

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.services.application_status import StatusRefused
from app.models.import_session import (
    CheckpointType,
    ImportCheckpoint,
    ImportSession,
    ImportStatus,
    StagingElement,
    StagingElementType,
)

logger = logging.getLogger(__name__)


class ImportStagingService:
    """
    Service for managing transactional import staging with checkpointing.

    Provides:
    - Session creation and management
    - Checkpoint-based progress tracking
    - Staging area for generated elements
    - Atomic commit operations
    - Recovery from failures
    """

    def __init__(self):
        self.logger = logger

    def create_session(
        self,
        user_id: int,
        filename: str,
        file_content: bytes = None,
        import_type: str = "application_import",
        enable_ai_import: bool = True,
        confidence_threshold: float = 0.7,
        archimate_mode: str = "standard",
        custom_mappings: Dict = None,
        total_rows: int = 0,
    ) -> ImportSession:
        """
        Create a new import session.

        Args:
            user_id: User performing the import
            filename: Name of uploaded file
            file_content: Optional file content for hash generation
            import_type: Type of import (application_import, archimate_import, etc.)
            enable_ai_import: Whether to use AI-powered import
            confidence_threshold: Minimum confidence for auto-approval
            archimate_mode: ArchiMate generation mode (quick, standard, comprehensive)
            custom_mappings: User-defined field mappings
            total_rows: Total number of rows to process

        Returns:
            Created ImportSession
        """
        try:
            session_uuid = str(uuid.uuid4())

            # Generate file hash for deduplication
            file_hash = None
            if file_content:
                file_hash = hashlib.sha256(file_content).hexdigest()

            session = ImportSession(
                session_uuid=session_uuid,
                user_id=user_id,
                filename=filename,
                file_size_bytes=len(file_content) if file_content else None,
                file_hash=file_hash,
                import_type=import_type,
                enable_ai_import=enable_ai_import,
                confidence_threshold=confidence_threshold,
                archimate_mode=archimate_mode,
                custom_mappings=custom_mappings,
                status=ImportStatus.PENDING,
                total_rows=total_rows,
                processed_rows=0,
                successful_rows=0,
                failed_rows=0,
                skipped_rows=0,
                progress_percentage=0.0,
                llm_calls_made=0,
                llm_tokens_used=0,
                estimated_cost_usd=0.0,
                retry_count=0,
                recovery_attempts=0,
                can_resume=True,
            )

            db.session.add(session)
            db.session.commit()

            self.logger.info(f"Created import session {session_uuid} for user {user_id}")

            # Create initial checkpoint
            self.add_checkpoint(
                session.id,
                CheckpointType.FILE_UPLOADED,
                "File uploaded successfully",
                checkpoint_data={"filename": filename, "total_rows": total_rows},
            )

            return session

        except SQLAlchemyError as e:
            db.session.rollback()
            self.logger.error(f"Failed to create import session: {e}")
            raise

    def get_session(
        self, session_id: int = None, session_uuid: str = None
    ) -> Optional[ImportSession]:
        """Get import session by ID or UUID."""
        if session_id:
            return ImportSession.query.get(session_id)
        elif session_uuid:
            return ImportSession.query.filter_by(session_uuid=session_uuid).first()
        return None

    def get_recoverable_sessions(self, user_id: int) -> List[ImportSession]:
        """Get sessions that can be recovered/resumed."""
        return (
            ImportSession.query.filter(
                and_(
                    ImportSession.user_id == user_id,
                    ImportSession.can_resume == True,
                    or_(
                        ImportSession.status == ImportStatus.FAILED,
                        ImportSession.status == ImportStatus.PAUSED,
                        ImportSession.status == ImportStatus.IN_PROGRESS,
                    ),
                )
            )
            .order_by(ImportSession.last_activity_at.desc())
            .all()
        )

    def start_session(self, session_id: int) -> ImportSession:
        """Mark session as started."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.status = ImportStatus.IN_PROGRESS
        session.started_at = datetime.utcnow()
        session.last_activity_at = datetime.utcnow()

        db.session.commit()

        self.logger.info(f"Started import session {session.session_uuid}")
        return session

    def resume_session(self, session_id: int) -> Tuple[ImportSession, str]:
        """
        Resume a paused or failed session.

        Returns:
            Tuple of (session, checkpoint_to_resume_from)
        """
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if not session.can_resume:
            raise ValueError(f"Session {session.session_uuid} cannot be resumed")

        session.status = ImportStatus.RECOVERING
        session.recovery_attempts += 1
        session.last_activity_at = datetime.utcnow()

        # Determine checkpoint to resume from
        resume_checkpoint = session.resume_from_checkpoint or session.current_checkpoint

        db.session.commit()

        self.logger.info(
            f"Resuming import session {session.session_uuid} from checkpoint {resume_checkpoint}"
        )

        return session, resume_checkpoint

    def complete_session(self, session_id: int, results_summary: Dict = None) -> ImportSession:
        """Mark session as completed."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.status = ImportStatus.COMPLETED
        session.completed_at = datetime.utcnow()
        session.last_activity_at = datetime.utcnow()
        session.can_resume = False

        if session.started_at:
            session.processing_time_seconds = int(
                (session.completed_at - session.started_at).total_seconds()
            )

        if results_summary:
            session.results_summary = results_summary

        db.session.commit()

        self.logger.info(f"Completed import session {session.session_uuid}")

        # Add final checkpoint
        self.add_checkpoint(
            session.id,
            CheckpointType.FINAL_COMMIT,
            "Import completed successfully",
            checkpoint_data=results_summary,
        )

        return session

    def fail_session(
        self,
        session_id: int,
        error_message: str,
        error_details: Dict = None,
        can_resume: bool = True,
    ) -> ImportSession:
        """Mark session as failed."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.status = ImportStatus.FAILED
        session.record_error(error_message, error_details)
        session.can_resume = can_resume
        session.last_activity_at = datetime.utcnow()

        db.session.commit()

        self.logger.error(f"Failed import session {session.session_uuid}: {error_message}")

        return session

    def add_checkpoint(
        self,
        session_id: int,
        checkpoint_type: CheckpointType,
        checkpoint_name: str,
        checkpoint_data: Dict = None,
        duration_seconds: int = None,
    ) -> ImportCheckpoint:
        """
        Add a checkpoint marker for recovery.

        Args:
            session_id: Import session ID
            checkpoint_type: Type of checkpoint
            checkpoint_name: Human-readable checkpoint name
            checkpoint_data: Optional checkpoint-specific data
            duration_seconds: Time taken for this checkpoint step

        Returns:
            Created ImportCheckpoint
        """
        try:
            session = self.get_session(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")

            checkpoint = ImportCheckpoint(
                import_session_id=session_id,
                checkpoint_type=checkpoint_type.value,
                checkpoint_name=checkpoint_name,
                checkpoint_data=checkpoint_data,
                rows_processed=session.processed_rows,
                elements_staged=session.staging_elements.count(),
                duration_seconds=duration_seconds,
            )

            db.session.add(checkpoint)

            # Update session checkpoint
            session.add_checkpoint(checkpoint_type, checkpoint_data)

            db.session.commit()

            self.logger.info(
                f"Added checkpoint {checkpoint_type.value} to session {session.session_uuid}"
            )

            return checkpoint

        except SQLAlchemyError as e:
            db.session.rollback()
            self.logger.error(f"Failed to add checkpoint: {e}")
            raise

    def stage_element(
        self,
        session_id: int,
        element_type: StagingElementType,
        element_data: Dict,
        source_row_number: int = None,
        source_data: Dict = None,
        generated_by_llm: bool = False,
        llm_provider: str = None,
        llm_model: str = None,
        confidence_score: float = None,
        requires_review: bool = False,
        parent_element_uuid: str = None,
        related_element_uuids: List[str] = None,
        metadata: Dict = None,
    ) -> StagingElement:
        """
        Stage an element for later commit.

        Args:
            session_id: Import session ID
            element_type: Type of element being staged
            element_data: Full element data
            source_row_number: Row number in original file
            source_data: Original CSV/Excel row data
            generated_by_llm: Whether element was generated by LLM
            llm_provider: LLM provider used (if applicable)
            llm_model: LLM model used (if applicable)
            confidence_score: Confidence score for LLM-generated elements
            requires_review: Whether element requires human review
            parent_element_uuid: UUID of parent element (for hierarchies)
            related_element_uuids: List of related element UUIDs
            metadata: Additional metadata

        Returns:
            Created StagingElement
        """
        try:
            element_uuid = str(uuid.uuid4())

            staging_element = StagingElement(
                import_session_id=session_id,
                element_type=element_type.value,
                element_uuid=element_uuid,
                element_data=element_data,
                metadata=metadata,
                source_row_number=source_row_number,
                source_data=source_data,
                generated_by_llm=generated_by_llm,
                llm_provider=llm_provider,
                llm_model=llm_model,
                confidence_score=confidence_score,
                requires_review=requires_review,
                parent_element_uuid=parent_element_uuid,
                related_element_uuids=related_element_uuids,
                is_committed=False,
            )

            db.session.add(staging_element)
            db.session.commit()

            self.logger.debug(
                f"Staged {element_type.value} element {element_uuid} for session {session_id}"
            )

            return staging_element

        except SQLAlchemyError as e:
            db.session.rollback()
            self.logger.error(f"Failed to stage element: {e}")
            raise

    def get_staged_elements(
        self, session_id: int, element_type: StagingElementType = None, committed: bool = False
    ) -> List[StagingElement]:
        """Get staged elements for a session."""
        query = StagingElement.query.filter_by(import_session_id=session_id, is_committed=committed)

        if element_type:
            query = query.filter_by(element_type=element_type.value)

        return query.order_by(StagingElement.created_at).all()

    def commit_staged_elements(
        self, session_id: int, element_type: StagingElementType = None
    ) -> Tuple[int, List[int]]:
        """
        Commit staged elements to permanent tables.

        Args:
            session_id: Import session ID
            element_type: Optional filter for specific element type

        Returns:
            Tuple of (count_committed, list_of_committed_ids)
        """
        try:
            # Get uncommitted elements
            elements = self.get_staged_elements(session_id, element_type, committed=False)

            committed_count = 0
            committed_ids = []

            for element in elements:
                # Commit element based on type
                try:
                    committed_id = self._commit_element_by_type(element)
                except StatusRefused as refusal:
                    # One refused row is left staged for review; the rest are kept.
                    self.logger.warning(f"Refused staged element {element.element_uuid}: {refusal}")
                    element.requires_review = True
                    element.element_metadata = {
                        **(element.element_metadata or {}),
                        "refusal": str(refusal),
                    }
                    continue

                if committed_id:
                    # Mark as committed
                    element.is_committed = True
                    element.committed_at = datetime.utcnow()
                    element.committed_id = committed_id

                    committed_count += 1
                    committed_ids.append(committed_id)

            db.session.commit()

            self.logger.info(
                f"Committed {committed_count} staged elements for session {session_id}"
            )

            return committed_count, committed_ids

        except SQLAlchemyError as e:
            db.session.rollback()
            self.logger.error(f"Failed to commit staged elements: {e}")
            raise

    def _commit_element_by_type(self, element: StagingElement) -> Optional[int]:
        """
        Commit a single staged element to its permanent table.

        Returns:
            ID of committed element in permanent table
        """
        element_type = element.element_type
        element_data = element.element_data

        try:
            if element_type == StagingElementType.APPLICATION.value:
                from app.models.application_portfolio import ApplicationComponent

                app = ApplicationComponent(**element_data)
                db.session.add(app)
                db.session.flush()
                return app.id

            elif element_type == StagingElementType.CAPABILITY.value:
                from app.models.unified_capability import UnifiedCapability

                capability = UnifiedCapability(**element_data)
                db.session.add(capability)
                db.session.flush()
                return capability.id

            elif element_type == StagingElementType.ARCHIMATE_ELEMENT.value:
                from app.models.archimate_core import ArchiMateElement

                archimate = ArchiMateElement(**element_data)
                db.session.add(archimate)
                db.session.flush()
                return archimate.id

            elif element_type == StagingElementType.ARCHIMATE_RELATIONSHIP.value:
                from app.models.archimate_core import ArchiMateRelationship

                relationship = ArchiMateRelationship(**element_data)
                db.session.add(relationship)
                db.session.flush()
                return relationship.id

            elif element_type == StagingElementType.APQC_MAPPING.value:
                from app.models.apqc_process import ProcessApplicationMapping

                mapping = ProcessApplicationMapping(**element_data)
                db.session.add(mapping)
                db.session.flush()
                return mapping.id

            else:
                self.logger.warning(f"Unknown element type: {element_type}")
                return None

        except Exception as e:
            self.logger.error(f"Failed to commit element {element.element_uuid}: {e}")
            raise

    def rollback_session(self, session_id: int) -> int:
        """
        Rollback a session by deleting all staged elements.

        Returns:
            Number of elements deleted
        """
        try:
            elements = self.get_staged_elements(session_id, committed=False)
            count = len(elements)

            for element in elements:
                db.session.delete(element)

            db.session.commit()

            self.logger.info(f"Rolled back {count} staged elements for session {session_id}")

            return count

        except SQLAlchemyError as e:
            db.session.rollback()
            self.logger.error(f"Failed to rollback session: {e}")
            raise

    def update_progress(
        self,
        session_id: int,
        processed: int = None,
        successful: int = None,
        failed: int = None,
        skipped: int = None,
    ) -> ImportSession:
        """Update session progress counters."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.update_progress(processed, successful, failed, skipped)
        db.session.commit()

        return session

    def track_llm_usage(
        self, session_id: int, provider: str, tokens: int, estimated_cost: float
    ) -> ImportSession:
        """Track LLM API usage and costs."""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.track_llm_usage(provider, tokens, estimated_cost)
        db.session.commit()

        return session


# Singleton instance
_import_staging_service = None


def get_import_staging_service() -> ImportStagingService:
    """Get singleton instance of ImportStagingService."""
    global _import_staging_service
    if _import_staging_service is None:
        _import_staging_service = ImportStagingService()
    return _import_staging_service
