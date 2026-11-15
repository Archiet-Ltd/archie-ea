"""
Batch Import Models

Database models for the batch import system with approval workflow.
Provides resilient, checkpoint-based import processing with user review.

Models:
- BatchImportJob: Master record for an import operation
- BatchImportBatch: Individual batch within a job
- BatchImportApplication: Application being imported
- BatchImportElement: AI-generated element staged for approval
- BatchImportCheckpoint: Recovery checkpoint
"""

import enum
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional  # dead-code-ok

_state_logger = logging.getLogger(__name__)

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import event
from sqlalchemy.orm import relationship

from app import db

# =============================================================================
# ENUMS
# =============================================================================


class BatchJobStatus(str, enum.Enum):
    """Status of a batch import job."""

    PENDING = "pending"  # Job created, not started
    ESTIMATING = "estimating"  # Calculating cost estimate
    AWAITING_CONFIRMATION = (
        "awaiting_confirmation"  # Waiting for user to confirm budget
    )
    PROCESSING = "processing"  # Batches being processed
    PAUSED = "paused"  # User paused
    COMPLETED = "completed"  # All batches processed and committed
    CANCELLED = "cancelled"  # User cancelled
    FAILED = "failed"  # Unrecoverable error

    @classmethod
    def validate_transition(cls, current, target):
        """Validate a status transition. Returns True if valid, raises ValueError if not."""
        # Valid state transitions (from -> set of allowed to states)
        TRANSITIONS = {
            "pending": {
                "estimating",
                "cancelled",
                "awaiting_confirmation",
            },  # Fixed: allow direct to awaiting (skip estimate if free?)
            "estimating": {"awaiting_confirmation", "failed", "cancelled"},
            "awaiting_confirmation": {"processing", "cancelled"},
            "processing": {"paused", "completed", "cancelled", "failed"},
            "paused": {"processing", "cancelled"},
            "completed": set(),  # terminal
            "cancelled": set(),  # terminal
            "failed": set(),  # terminal
        }

        current_val = current.value if isinstance(current, cls) else current
        target_val = target.value if isinstance(target, cls) else target

        allowed = TRANSITIONS.get(current_val, set())
        if target_val not in allowed:
            # Allow same-state transitions (idempotency)
            if current_val == target_val:
                return True

            raise ValueError(
                f"Invalid job status transition: {current_val} -> {target_val}. "
                f"Allowed: {allowed or 'none (terminal state)'}"
            )
        _state_logger.debug("Job status: %s -> %s", current_val, target_val)
        return True


class BatchStatus(str, enum.Enum):
    """Status of an individual batch."""

    QUEUED = "queued"  # Waiting to be processed
    PROCESSING = "processing"  # Currently being processed
    READY_FOR_REVIEW = "ready_for_review"  # Waiting for user approval
    APPROVED = "approved"  # User approved, ready to commit
    REJECTED = "rejected"  # User rejected
    COMMITTED = "committed"  # Elements committed to database
    FAILED = "failed"  # Processing failed
    SKIPPED = "skipped"  # User chose to skip

    # enum.nonmember: a plain dict inside an Enum body becomes an enum MEMBER,
    # so cls._TRANSITIONS.get(...) raised "'BatchStatus' object has no
    # attribute 'get'" — every batch processing attempt 500'd since this
    # state machine shipped.
    _TRANSITIONS = enum.nonmember({
        "queued": {"processing", "skipped"},
        "processing": {"ready_for_review", "failed", "queued"},
        "ready_for_review": {"approved", "rejected"},
        "approved": {"committed", "rejected"},
        "rejected": set(),
        "committed": set(),
        "failed": {"queued"},
        "skipped": set(),
    })

    @classmethod
    def validate_transition(cls, current, target):
        """Validate a status transition. Returns True if valid, raises ValueError if not."""
        current_val = current.value if isinstance(current, cls) else current
        target_val = target.value if isinstance(target, cls) else target
        allowed = cls._TRANSITIONS.get(current_val, set())
        if target_val not in allowed:
            raise ValueError(
                f"Invalid batch status transition: {current_val} -> {target_val}. "
                f"Allowed: {allowed or 'none (terminal state)'}"
            )
        _state_logger.debug("Batch status: %s -> %s", current_val, target_val)
        return True


class AppProcessingStatus(str, enum.Enum):
    """Status of an application within a batch."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMMITTED = "committed"
    FAILED = "failed"
    SKIPPED = "skipped"

    # enum.nonmember — same fix as BatchStatus._TRANSITIONS above.
    _TRANSITIONS = enum.nonmember({
        "pending": {"processing", "skipped"},
        "processing": {"completed", "failed"},
        "completed": {"committed"},
        "committed": set(),
        "failed": {"processing"},
        "skipped": set(),
    })

    @classmethod
    def validate_transition(cls, current, target):
        """Validate a status transition. Returns True if valid, raises ValueError if not."""
        current_val = current.value if isinstance(current, cls) else current
        target_val = target.value if isinstance(target, cls) else target
        allowed = cls._TRANSITIONS.get(current_val, set())
        if target_val not in allowed:
            raise ValueError(
                f"Invalid app status transition: {current_val} -> {target_val}. "
                f"Allowed: {allowed or 'none (terminal state)'}"
            )
        _state_logger.debug("App status: %s -> %s", current_val, target_val)
        return True


class ElementApprovalStatus(str, enum.Enum):
    """Approval status of a staged element."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"  # User edited before approving


class CheckpointType(str, enum.Enum):
    """Type of checkpoint."""

    JOB_STARTED = "job_started"
    BATCH_STARTED = "batch_started"
    APP_STARTED = "app_started"
    APP_ELEMENTS_GENERATED = "app_elements_generated"
    APP_COMPLETED = "app_completed"
    BATCH_COMPLETED = "batch_completed"
    BATCH_APPROVED = "batch_approved"
    BATCH_COMMITTED = "batch_committed"


# =============================================================================
# MODELS
# =============================================================================


class BatchImportJob(db.Model):
    """
    Master record for a batch import operation.

    Tracks overall progress, cost, and settings for the entire import job.
    """

    __tablename__ = "batch_import_job"

    id = Column(Integer, primary_key=True)
    job_uuid = Column(
        String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4())
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # User-entered job name (distinct from the uploaded/pasted file's filename).
    # Nullable: reconcile-schema only adds nullable columns to existing installs.
    name = Column(String(255))

    # File info
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500))  # Stored file location
    file_hash = Column(String(64))  # SHA - 256 for deduplication
    total_applications = Column(Integer, nullable=False, default=0)

    # Batch configuration
    batch_size = Column(Integer, default=20)
    total_batches = Column(Integer, nullable=False, default=0)

    # Progress tracking
    status = Column(
        Enum(BatchJobStatus), default=BatchJobStatus.PENDING, nullable=False
    )
    batches_completed = Column(Integer, default=0)
    batches_approved = Column(Integer, default=0)
    batches_rejected = Column(Integer, default=0)
    batches_committed = Column(Integer, default=0)

    # Cost tracking
    estimated_cost_usd = Column(Numeric(10, 4), default=Decimal("0"))
    actual_cost_usd = Column(Numeric(10, 4), default=Decimal("0"))
    budget_limit_usd = Column(Numeric(10, 4))  # User-defined budget limit

    # LLM tracking
    total_llm_calls = Column(Integer, default=0)
    total_tokens_used = Column(Integer, default=0)
    llm_providers_used = Column(JSON, default=list)  # ["gpt - 4", "claude - 3"]

    # Import settings
    enable_ai_generation = Column(Boolean, default=True)
    archimate_mode = Column(
        String(20), default="standard"
    )  # quick/standard/comprehensive
    auto_approve_high_confidence = Column(Boolean, default=False)
    confidence_threshold = Column(Float, default=0.85)
    custom_field_mappings = Column(JSON, default=dict)  # Field mapping overrides

    # Error tracking
    error_message = Column(Text)
    error_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    batches = relationship(
        "BatchImportBatch",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="BatchImportBatch.batch_number",
    )
    user = relationship("User")

    # Indexes
    __table_args__ = (
        Index("ix_batch_import_job_user_status", "user_id", "status"),
        Index("ix_batch_import_job_created", "created_at"),
    )

    def __repr__(self):
        return f"<BatchImportJob {self.job_uuid} - {self.filename}>"

    @property
    def progress_percentage(self) -> float:
        """Calculate overall progress percentage."""
        if self.total_batches == 0:
            return 0.0
        completed = self.batches_completed or 0
        return round((completed / self.total_batches) * 100, 1)

    @property
    def batches_ready_for_review(self) -> int:
        """Count of batches waiting for review."""
        return sum(1 for b in self.batches if b.status == BatchStatus.READY_FOR_REVIEW)

    @property
    def cost_remaining(self) -> Decimal:
        """Remaining budget."""
        if self.budget_limit_usd is None:
            return None
        return self.budget_limit_usd - self.actual_cost_usd

    def _cost_variance(self) -> Optional[Dict[str, Any]]:
        """Calculate estimated vs actual cost variance."""
        estimated = float(self.estimated_cost_usd) if self.estimated_cost_usd else 0
        actual = float(self.actual_cost_usd) if self.actual_cost_usd else 0
        if estimated == 0 and actual == 0:
            return None
        variance = actual - estimated
        pct = (variance / estimated * 100) if estimated > 0 else None
        return {
            "estimated": estimated,
            "actual": actual,
            "variance_usd": round(variance, 4),
            "variance_pct": round(pct, 1) if pct is not None else None,
            "over_budget": pct is not None and pct > 20,
        }

    def record_token_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_per_input: float = 0.003,
        cost_per_output: float = 0.015,
    ):
        """Record actual token usage from an API response and update actual cost."""
        tokens = input_tokens + output_tokens
        self.total_tokens_used = (self.total_tokens_used or 0) + tokens
        cost = Decimal(str(input_tokens / 1000 * cost_per_input)) + Decimal(
            str(output_tokens / 1000 * cost_per_output)
        )
        self.actual_cost_usd = (self.actual_cost_usd or Decimal("0")) + cost

    def set_status(self, new_status):
        """Set job status with transition validation."""
        BatchJobStatus.validate_transition(self.status, new_status)
        self.status = new_status

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        # Calculate total elements generated from batches
        elements_generated = sum(b.total_elements_generated or 0 for b in self.batches)

        return {
            "id": self.id,
            "job_uuid": self.job_uuid,
            "name": self.name or self.filename,  # Frontend expects 'name'
            "filename": self.filename,
            "mode": self.archimate_mode,  # Frontend expects 'mode'
            "total_applications": self.total_applications,
            "applications_count": self.total_applications,  # Frontend expects 'applications_count'
            "batch_size": self.batch_size,
            "total_batches": self.total_batches,
            "status": self.status.value if self.status else None,
            "batches_completed": self.batches_completed or 0,
            "processed_batches": self.batches_completed or 0,  # Frontend expects 'processed_batches'
            "batches_approved": self.batches_approved or 0,
            "batches_rejected": self.batches_rejected or 0,
            "batches_committed": self.batches_committed or 0,
            "batches_ready_for_review": self.batches_ready_for_review,
            "progress": self.progress_percentage,  # Frontend expects 'progress'
            "progress_percentage": self.progress_percentage,
            "elements_generated": elements_generated,  # Frontend expects 'elements_generated'
            "estimated_cost": float(self.estimated_cost_usd)
            if self.estimated_cost_usd
            else 0,
            "estimated_cost_usd": float(self.estimated_cost_usd)
            if self.estimated_cost_usd
            else 0,
            "actual_cost": float(self.actual_cost_usd)
            if self.actual_cost_usd
            else 0,  # Frontend expects 'actual_cost'
            "actual_cost_usd": float(self.actual_cost_usd)
            if self.actual_cost_usd
            else 0,
            "budget_limit_usd": float(self.budget_limit_usd)
            if self.budget_limit_usd
            else None,
            "cost_variance": self._cost_variance(),
            "total_tokens_used": self.total_tokens_used or 0,
            "enable_ai_generation": self.enable_ai_generation,
            "archimate_mode": self.archimate_mode,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
        }


class BatchImportBatch(db.Model):
    """
    Individual batch within an import job.

    Each batch contains a subset of applications to process together.
    """

    __tablename__ = "batch_import_batch"

    id = Column(Integer, primary_key=True)
    job_id = Column(
        Integer, ForeignKey("batch_import_job.id", ondelete="CASCADE"), nullable=False
    )
    batch_number = Column(Integer, nullable=False)  # 1, 2, 3...

    # Status
    status = Column(Enum(BatchStatus), default=BatchStatus.QUEUED, nullable=False)

    # Progress within batch
    total_applications = Column(Integer, nullable=False, default=0)
    processed_applications = Column(Integer, default=0)
    successful_applications = Column(Integer, default=0)
    failed_applications = Column(Integer, default=0)
    current_application_name = Column(String(255))  # For UI display

    # Results
    total_elements_generated = Column(Integer, default=0)
    elements_approved = Column(Integer, default=0)
    elements_rejected = Column(Integer, default=0)

    # Cost for this batch
    batch_cost_usd = Column(Numeric(10, 4), default=Decimal("0"))
    batch_tokens_used = Column(Integer, default=0)
    batch_llm_calls = Column(Integer, default=0)

    # Review info
    reviewed_by_id = Column(Integer, ForeignKey("users.id"))
    reviewed_at = Column(DateTime)
    review_notes = Column(Text)

    # Error handling
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)

    # Priority for queue
    priority = Column(Integer, default=0)  # Higher = more urgent

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    committed_at = Column(DateTime)

    # Relationships
    job = relationship("BatchImportJob", back_populates="batches")
    applications = relationship(
        "BatchImportApplication",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="BatchImportApplication.row_number",
    )
    elements = relationship(
        "BatchImportElement",
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    checkpoints = relationship(
        "BatchImportCheckpoint",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="BatchImportCheckpoint.created_at",
    )
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])

    # Indexes
    __table_args__ = (
        Index("ix_batch_import_batch_job_status", "job_id", "status"),
        Index("ix_batch_import_batch_status_priority", "status", "priority"),
    )

    def __repr__(self):
        return f"<BatchImportBatch {self.id} - Job {self.job_id} Batch #{self.batch_number}>"

    @property
    def progress_percentage(self) -> float:
        """Calculate batch progress percentage."""
        if self.total_applications == 0:
            return 0.0
        processed = self.processed_applications or 0
        return round((processed / self.total_applications) * 100, 1)

    @property
    def can_retry(self) -> bool:
        """Check if batch can be retried."""
        return self.status == BatchStatus.FAILED and self.retry_count < self.max_retries

    def set_status(self, new_status):
        """Set batch status with transition validation."""
        BatchStatus.validate_transition(self.status, new_status)
        self.status = new_status

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "batch_number": self.batch_number,
            "status": self.status.value if self.status else None,
            "total_applications": self.total_applications,
            "processed_applications": self.processed_applications or 0,
            "successful_applications": self.successful_applications or 0,
            "failed_applications": self.failed_applications or 0,
            "progress_percentage": self.progress_percentage,
            "current_application_name": self.current_application_name,
            "total_elements_generated": self.total_elements_generated or 0,
            "elements_approved": self.elements_approved or 0,
            "elements_rejected": self.elements_rejected or 0,
            "batch_cost_usd": float(self.batch_cost_usd) if self.batch_cost_usd else 0,
            "error_message": self.error_message,
            "retry_count": self.retry_count or 0,
            "can_retry": self.can_retry,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "review_notes": self.review_notes,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
        }


class BatchImportApplication(db.Model):
    """
    Individual application being imported within a batch.

    Tracks source data, processing status, and generated elements.
    """

    __tablename__ = "batch_import_application"

    id = Column(Integer, primary_key=True)
    batch_id = Column(
        Integer, ForeignKey("batch_import_batch.id", ondelete="CASCADE"), nullable=False
    )

    # Source data
    row_number = Column(Integer, nullable=False)  # Original row in file
    source_data = Column(JSON, nullable=False)  # Original row data from file

    # Application info (extracted)
    application_name = Column(String(255), nullable=False)
    application_description = Column(Text)
    application_type = Column(String(100))
    vendor_name = Column(String(255))

    # Processing status
    status = Column(
        Enum(AppProcessingStatus), default=AppProcessingStatus.PENDING, nullable=False
    )

    # Result
    committed_application_id = Column(Integer, ForeignKey("application_components.id"))
    error_message = Column(Text)

    # AI generation stats
    elements_generated = Column(Integer, default=0)
    average_confidence_score = Column(Float)
    processing_time_seconds = Column(Float)

    # LLM usage for this application
    llm_calls = Column(Integer, default=0)
    tokens_used = Column(Integer, default=0)
    cost_usd = Column(Numeric(10, 4), default=Decimal("0"))

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime)

    # Relationships
    batch = relationship("BatchImportBatch", back_populates="applications")
    elements = relationship(
        "BatchImportElement",
        back_populates="application",
        cascade="all, delete-orphan",
    )
    committed_application = relationship("ApplicationComponent")

    # Indexes
    __table_args__ = (
        Index("ix_batch_import_app_batch_status", "batch_id", "status"),
        Index("ix_batch_import_app_name", "application_name"),
    )

    def __repr__(self):
        return f"<BatchImportApplication {self.id} - {self.application_name}>"

    def set_status(self, new_status):
        """Set application status with transition validation."""
        AppProcessingStatus.validate_transition(self.status, new_status)
        self.status = new_status

    def record_token_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_per_input: float = 0.003,
        cost_per_output: float = 0.015,
    ):
        """Record actual token usage from an API response."""
        tokens = input_tokens + output_tokens
        self.tokens_used = (self.tokens_used or 0) + tokens
        self.llm_calls = (self.llm_calls or 0) + 1
        cost = Decimal(str(input_tokens / 1000 * cost_per_input)) + Decimal(
            str(output_tokens / 1000 * cost_per_output)
        )
        self.cost_usd = (self.cost_usd or Decimal("0")) + cost

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "row_number": self.row_number,
            "application_name": self.application_name,
            "application_description": self.application_description,
            "application_type": self.application_type,
            "vendor_name": self.vendor_name,
            "status": self.status.value if self.status else None,
            "elements_generated": self.elements_generated,
            "average_confidence_score": self.average_confidence_score,
            "error_message": self.error_message,
            "committed_application_id": self.committed_application_id,
            "tokens_used": self.tokens_used or 0,
            "cost_usd": float(self.cost_usd) if self.cost_usd else 0,
            "processed_at": self.processed_at.isoformat()
            if self.processed_at
            else None,
        }


class BatchImportElement(db.Model):
    """
    AI-generated element staged for approval.

    Stores all generated elements (ArchiMate, capabilities, etc.) until approved.
    """

    __tablename__ = "batch_import_element"

    id = Column(Integer, primary_key=True)
    element_uuid = Column(
        String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4())
    )
    batch_id = Column(
        Integer, ForeignKey("batch_import_batch.id", ondelete="CASCADE"), nullable=False
    )
    application_id = Column(
        Integer,
        ForeignKey("batch_import_application.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Element identification
    element_type = Column(
        String(50), nullable=False
    )  # archimate_element, capability_mapping, etc.
    element_subtype = Column(String(50))  # business_actor, application_component, etc.

    # Element data
    element_name = Column(String(255), nullable=False)
    element_description = Column(Text)
    element_data = Column(JSON, nullable=False)  # Full element details

    # ArchiMate specific
    archimate_layer = Column(
        String(50)
    )  # motivation, strategy, business, application, technology, physical, implementation

    # AI metadata
    generated_by_model = Column(String(100))  # gpt - 4, claude - 3, etc.
    confidence_score = Column(Float)
    generation_prompt_hash = Column(String(64))  # For deduplication

    # Approval status
    approval_status = Column(
        Enum(ElementApprovalStatus),
        default=ElementApprovalStatus.PENDING,
        nullable=False,
    )
    approved_by_id = Column(Integer, ForeignKey("users.id"))
    approved_at = Column(DateTime)
    rejection_reason = Column(Text)

    # If modified by user
    original_data = Column(JSON)  # Original AI output (preserved if modified)
    modified_data = Column(JSON)  # User modifications
    is_modified = Column(Boolean, default=False)

    # Commit tracking
    is_committed = Column(Boolean, default=False)
    committed_at = Column(DateTime)
    committed_record_id = Column(Integer)  # ID in target table
    committed_table = Column(String(100))  # Target table name

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    batch = relationship("BatchImportBatch", back_populates="elements")
    application = relationship("BatchImportApplication", back_populates="elements")
    approved_by = relationship("User", foreign_keys=[approved_by_id])

    # Indexes
    __table_args__ = (
        Index("ix_batch_import_element_batch", "batch_id"),
        Index("ix_batch_import_element_app", "application_id"),
        Index("ix_batch_import_element_type", "element_type"),
        Index("ix_batch_import_element_approval", "approval_status"),
        Index("ix_batch_import_element_layer", "archimate_layer"),
    )

    def __repr__(self):
        return f"<BatchImportElement {self.element_uuid} - {self.element_type}:{self.element_name}>"

    def to_dict(self, include_data: bool = True) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        result = {
            "id": self.id,
            "element_uuid": self.element_uuid,
            "batch_id": self.batch_id,
            "application_id": self.application_id,
            "element_type": self.element_type,
            "element_subtype": self.element_subtype,
            "element_name": self.element_name,
            "element_description": self.element_description,
            "archimate_layer": self.archimate_layer,
            "generated_by_model": self.generated_by_model,
            "confidence_score": self.confidence_score,
            "approval_status": self.approval_status.value
            if self.approval_status
            else None,
            "is_modified": self.is_modified,
            "is_committed": self.is_committed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_data:
            result["element_data"] = (
                self.modified_data if self.is_modified else self.element_data
            )
            result["original_data"] = self.original_data if self.is_modified else None
        return result


class BatchImportCheckpoint(db.Model):
    """
    Recovery checkpoint for batch processing.

    Enables resume from exact point of failure.
    """

    __tablename__ = "batch_import_checkpoint"

    id = Column(Integer, primary_key=True)
    batch_id = Column(
        Integer, ForeignKey("batch_import_batch.id", ondelete="CASCADE"), nullable=False
    )

    # Checkpoint info
    checkpoint_type = Column(Enum(CheckpointType), nullable=False)
    checkpoint_name = Column(String(100))  # Human-readable name

    # References
    application_id = Column(
        Integer, ForeignKey("batch_import_application.id", ondelete="SET NULL")
    )

    # State snapshot
    checkpoint_data = Column(JSON)  # Additional state data
    elements_staged = Column(Integer, default=0)  # Elements created up to this point

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    batch = relationship("BatchImportBatch", back_populates="checkpoints")

    # Indexes
    __table_args__ = (
        Index("ix_batch_import_checkpoint_batch", "batch_id", "created_at"),
    )

    def __repr__(self):
        return f"<BatchImportCheckpoint {self.id} - {self.checkpoint_type.value}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "checkpoint_type": self.checkpoint_type.value
            if self.checkpoint_type
            else None,
            "checkpoint_name": self.checkpoint_name,
            "application_id": self.application_id,
            "elements_staged": self.elements_staged,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ImportAuditLog(db.Model):
    """Audit trail for Import Applications workflow.

    Tracks every import operation with before/after snapshots so admins can
    review what changed, who imported it, and restore previous values.
    """

    __tablename__ = "import_audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_email = Column(String(255))
    import_type = Column(
        String(50), nullable=False, default="excel"
    )  # excel, manual, csv, json
    filename = Column(String(500))
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Summary
    records_created = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    records_skipped = Column(Integer, default=0)
    records_failed = Column(Integer, default=0)
    records_rollback = Column(Integer, default=0)  # For rollback operations
    duplicate_mode = Column(String(50))  # skip, merge, update, create

    # Detailed change log: list of {app_name, app_id, action, changed_fields: {field: {old, new}}}
    changes = Column(JSON)

    # Errors encountered
    errors = Column(JSON)

    __table_args__ = (Index("idx_import_audit_user_time", "user_id", "timestamp"),)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "import_type": self.import_type,
            "filename": self.filename,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "records_created": self.records_created,
            "records_updated": self.records_updated,
            "records_skipped": self.records_skipped,
            "records_failed": self.records_failed,
            "records_rollback": self.records_rollback,
            "duplicate_mode": self.duplicate_mode,
            "changes": self.changes,
            "errors": self.errors,
        }


# ============================================================================
# State Machine Enforcement via SQLAlchemy Events
# ============================================================================


def _validate_job_status_transition(target, value, oldvalue, initiator):
    """SQLAlchemy event listener that validates BatchImportJob status transitions."""
    if oldvalue is value or oldvalue is None:
        return value
    # Skip during initial load from DB
    if oldvalue is attributes.NO_VALUE or oldvalue is attributes.NEVER_SET:
        return value
    try:
        BatchJobStatus.validate_transition(oldvalue, value)
    except ValueError as e:
        _state_logger.warning("Invalid job status transition blocked: %s", e)
        raise
    return value


def _validate_batch_status_transition(target, value, oldvalue, initiator):
    """SQLAlchemy event listener that validates BatchImportBatch status transitions."""
    if oldvalue is value or oldvalue is None:
        return value
    if oldvalue is attributes.NO_VALUE or oldvalue is attributes.NEVER_SET:
        return value
    try:
        BatchStatus.validate_transition(oldvalue, value)
    except ValueError as e:
        _state_logger.warning("Invalid batch status transition blocked: %s", e)
        raise
    return value


from sqlalchemy.orm import attributes  # noqa: E402

event.listen(BatchImportJob.status, "set", _validate_job_status_transition, retval=True)
event.listen(
    BatchImportBatch.status, "set", _validate_batch_status_transition, retval=True
)
