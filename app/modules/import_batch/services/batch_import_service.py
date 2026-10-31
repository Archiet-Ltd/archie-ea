"""
-> app.modules.import_batch.services.batch_service

Batch Import Service

Main orchestrator for batch import operations with approval workflow.
Handles job creation, cost estimation, and overall job management.
"""

import hashlib
import logging
import math
import os
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app import db
from app.models.batch_import import (  # dead-code-ok
    AppProcessingStatus,
    BatchImportApplication,
    BatchImportBatch,
    BatchImportElement,
    BatchImportJob,
    BatchJobStatus,
    BatchStatus,
)

logger = logging.getLogger(__name__)



class ImportRefused(ValueError):
    """A refusal whose message is written for the person who asked, and is shown to them as it stands."""


class ActiveJobExists(ImportRefused):
    """The same file is already being imported by this person; says so, and names the job."""

    def __init__(self, job):
        self.job_id = job.id
        self.status = getattr(job.status, "value", str(job.status))
        super().__init__("An active import job already exists for this file (%s). Open it, or cancel or complete it "
                         "before importing the file again." % self.status.replace("_", " "))

class BatchImportService:
    """
    Main service for batch import operations.

    Handles:
    - Job creation from uploaded files
    - Cost estimation before processing
    - Job lifecycle management (start, pause, resume, cancel)
    - Progress tracking
    """

    # Cost estimation defaults (USD) — override via Flask config:
    #   IMPORT_COST_PER_APP_BASE, IMPORT_COST_CAPABILITY_MAPPING,
    #   IMPORT_COST_PROCESS_CLASSIFICATION, IMPORT_BATCH_SIZE
    _DEFAULT_COST_PER_APP_BASE = Decimal("0.02")
    _DEFAULT_COST_CAPABILITY_MAPPING = Decimal("0.01")
    _DEFAULT_COST_PROCESS_CLASSIFICATION = Decimal("0.01")

    # ArchiMate mode multipliers
    ARCHIMATE_MODE_MULTIPLIERS = {
        "quick": Decimal("1.0"),
        "standard": Decimal("2.5"),
        "comprehensive": Decimal("5.0"),
    }

    # Default batch size
    DEFAULT_BATCH_SIZE = 20

    # DEF-042, Capgemini dry-run: estimate_cost() (called from every
    # create_job(), file-upload path included) read
    # self.cost_per_app_base/cost_capability_mapping/cost_process_classification
    # — none of which were ever set anywhere, so every import job creation
    # raised AttributeError. The paste path was simply the first one this
    # audit could reach past the separate "No file provided" bug. These read
    # the Flask-config overrides the class docstring already promised
    # (falling back to the _DEFAULT_* constants) lazily, as properties: the
    # module-level `import_service = BatchImportService()` singleton is
    # constructed at import time, outside any app context, so reading
    # current_app.config in __init__ would break app boot.
    @property
    def cost_per_app_base(self) -> Decimal:
        return Decimal(str(current_app.config.get(
            "IMPORT_COST_PER_APP_BASE", self._DEFAULT_COST_PER_APP_BASE
        )))

    @property
    def cost_capability_mapping(self) -> Decimal:
        return Decimal(str(current_app.config.get(
            "IMPORT_COST_CAPABILITY_MAPPING", self._DEFAULT_COST_CAPABILITY_MAPPING
        )))

    @property
    def cost_process_classification(self) -> Decimal:
        return Decimal(str(current_app.config.get(
            "IMPORT_COST_PROCESS_CLASSIFICATION", self._DEFAULT_COST_PROCESS_CLASSIFICATION
        )))

    def create_job(
        self,
        user_id: int,
        file: FileStorage,
        batch_size: int = None,
        archimate_mode: str = "standard",
        enable_ai_generation: bool = True,
        budget_limit_usd: float = None,
        confidence_threshold: float = 0.85,
        auto_approve_high_confidence: bool = False,
        custom_field_mappings: Dict = None,
        name: str = None,
    ) -> BatchImportJob:
        """
        Create a new batch import job from an uploaded file.

        Args:
            user_id: ID of the user creating the job
            file: Uploaded file (CSV, Excel, JSON)
            batch_size: Number of applications per batch
            archimate_mode: AI generation mode (quick/standard/comprehensive)
            enable_ai_generation: Whether to generate AI elements
            budget_limit_usd: Maximum budget for LLM costs
            confidence_threshold: Minimum confidence for auto-approval
            auto_approve_high_confidence: Auto-approve elements above threshold
            custom_field_mappings: Custom field mappings for import

        Returns:
            Created BatchImportJob instance
        """
        batch_size = batch_size or self.DEFAULT_BATCH_SIZE

        # Check concurrent job limit per user
        max_concurrent_jobs = current_app.config.get("MAX_CONCURRENT_BATCH_JOBS", 3)
        active_job_count = BatchImportJob.query.filter(
            BatchImportJob.user_id == user_id,
            BatchImportJob.status.in_([
                BatchJobStatus.PENDING, 
                BatchJobStatus.ESTIMATING,
                BatchJobStatus.AWAITING_CONFIRMATION, 
                BatchJobStatus.PROCESSING,
                BatchJobStatus.PAUSED,
            ])
        ).count()
        
        if active_job_count >= max_concurrent_jobs:
            raise ImportRefused(
                f"Maximum concurrent jobs limit reached ({max_concurrent_jobs}). "
                f"You currently have {active_job_count} active jobs. "
                f"Please wait for some jobs to complete before creating new ones."
            )

        # Save the file
        file_path, file_hash = self._save_file(file)
        job_committed = False

        try:
            # Idempotency: reject if active job with same file hash exists
            active_statuses = [
                BatchJobStatus.PENDING, BatchJobStatus.ESTIMATING,
                BatchJobStatus.AWAITING_CONFIRMATION, BatchJobStatus.PROCESSING,
                BatchJobStatus.PAUSED,
            ]
            existing_job = BatchImportJob.query.filter(
                BatchImportJob.file_hash == file_hash,
                BatchImportJob.user_id == user_id,
                BatchImportJob.status.in_(active_statuses),
            ).first()
            if existing_job:
                raise ActiveJobExists(existing_job)

            # Parse the file to get application count
            applications_data = self._parse_file(file_path)
            total_applications = len(applications_data)

            if total_applications == 0:
                raise ImportRefused("No applications found in the uploaded file")

            # Calculate number of batches
            total_batches = math.ceil(total_applications / batch_size)

            # Create the job
            job = BatchImportJob(
                job_uuid=str(uuid.uuid4()),
                user_id=user_id,
                name=name,
                filename=secure_filename(file.filename) or "upload",
                file_path=file_path,
                file_hash=file_hash,
                total_applications=total_applications,
                batch_size=batch_size,
                total_batches=total_batches,
                status=BatchJobStatus.PENDING,
                archimate_mode=archimate_mode,
                enable_ai_generation=enable_ai_generation,
                budget_limit_usd=Decimal(str(budget_limit_usd)) if budget_limit_usd else None,
                confidence_threshold=confidence_threshold,
                auto_approve_high_confidence=auto_approve_high_confidence,
                custom_field_mappings=custom_field_mappings or {},
            )

            db.session.add(job)
            db.session.flush()  # Get job ID

            # Create batches and applications
            self._create_batches_and_applications(job, applications_data, batch_size)

            # Estimate costs
            cost_estimate = self.estimate_cost(job)
            job.estimated_cost_usd = Decimal(str(cost_estimate["estimated_total_usd"]))

            # Update status
            job.status = BatchJobStatus.AWAITING_CONFIRMATION

            db.session.commit()
            job_committed = True

            logger.info(
                f"Created batch import job {job.job_uuid} with {total_applications} "
                f"applications in {total_batches} batches"
            )

            return job

        except Exception:
            # Clean up the saved file if job creation failed
            if not job_committed and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info("Cleaned up upload file after failed job creation: %s", file_path)
                except Exception as cleanup_err:
                    logger.warning("Failed to clean up upload file %s: %s", file_path, cleanup_err)
            raise

    def _save_file(self, file: FileStorage) -> Tuple[str, str]:
        """Save uploaded file and return path and hash."""
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "uploads")
        batch_import_folder = os.path.join(upload_folder, "batch_import")
        os.makedirs(batch_import_folder, exist_ok=True)

        # Generate unique filename with path traversal protection
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        sanitized_name = secure_filename(file.filename) or "upload.csv"
        safe_name = f"{timestamp}_{unique_id}_{sanitized_name}"
        file_path = os.path.join(batch_import_folder, safe_name)

        # Save and calculate hash
        file.save(file_path)
        file_hash = self._calculate_file_hash(file_path)

        return file_path, file_hash

    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA - 256 hash of file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _parse_file(self, file_path: str) -> List[Dict[str, Any]]:
        """Parse file and return list of application data dictionaries."""
        file_ext = os.path.splitext(file_path)[1].lower()

        try:
            if file_ext == ".csv":
                df = pd.read_csv(file_path)
            elif file_ext in [".xlsx", ".xls"]:
                df = pd.read_excel(file_path)
            elif file_ext == ".json":
                df = pd.read_json(file_path)
            else:
                raise ImportRefused(f"Unsupported file format: {file_ext}")

            # Convert to list of dictionaries
            return df.to_dict("records")

        except Exception as e:
            logger.error("Import error parsing file %s: %s", file_path, e, exc_info=True)
            raise ImportRefused("Failed to parse file. Please check the file format and try again.")

    def _create_batches_and_applications(
        self,
        job: BatchImportJob,
        applications_data: List[Dict[str, Any]],
        batch_size: int,
    ) -> None:
        """Create batch and application records from parsed data."""
        # PROG-002: SAP landscape exports (SolMan system lists, Readiness
        # Check sheets) are normalised into the standard row shape first.
        from app.modules.import_batch.services.sap_landscape_profile import (
            SapLandscapeProfile,
        )

        applications_data = SapLandscapeProfile.maybe_normalize(applications_data)
        if not applications_data:
            raise ImportRefused("No importable rows found in file.")

        # Find name column
        name_column = self._find_name_column(applications_data[0].keys())

        batch_number = 1
        current_batch = None
        apps_in_current_batch = 0

        for row_number, row_data in enumerate(applications_data, start=1):
            # Create new batch if needed
            if current_batch is None or apps_in_current_batch >= batch_size:
                current_batch = BatchImportBatch(
                    job_id=job.id,
                    batch_number=batch_number,
                    status=BatchStatus.QUEUED,
                    total_applications=min(
                        batch_size, len(applications_data) - (batch_number - 1) * batch_size
                    ),
                )
                db.session.add(current_batch)
                db.session.flush()
                batch_number += 1
                apps_in_current_batch = 0

            # Extract application info
            app_name = str(row_data.get(name_column, f"Application {row_number}")).strip()
            app_description = str(
                row_data.get("description", row_data.get("Description", ""))
            ).strip()
            app_type = str(
                row_data.get("type", row_data.get("Type", row_data.get("application_type", "")))
            ).strip()
            vendor_name = str(
                row_data.get("vendor", row_data.get("Vendor", row_data.get("vendor_name", "")))
            ).strip()

            # Create application record
            application = BatchImportApplication(
                batch_id=current_batch.id,
                row_number=row_number,
                source_data=row_data,
                application_name=app_name if app_name else f"Application {row_number}",
                application_description=app_description if app_description else None,
                application_type=app_type if app_type else None,
                vendor_name=vendor_name if vendor_name else None,
                status=AppProcessingStatus.PENDING,
            )
            db.session.add(application)
            apps_in_current_batch += 1

    def _find_name_column(self, columns) -> str:
        """Find the column containing application names."""
        name_variants = [
            "name",
            "Name",
            "NAME",
            "application_name",
            "Application Name",
            "ApplicationName",
            "app_name",
            "App Name",
            "AppName",
            "title",
            "Title",
            "TITLE",
            "application",
            "Application",
            "APPLICATION",
        ]
        for variant in name_variants:
            if variant in columns:
                return variant
        # Return first column as fallback
        return list(columns)[0]

    def estimate_cost(self, job: BatchImportJob) -> Dict[str, Any]:
        """
        Estimate LLM costs for the import job.

        Returns:
            Dictionary with cost breakdown
        """
        base = self._estimate_cost_for_count(
            app_count=job.total_applications,
            mode=job.archimate_mode,
            enable_ai=bool(job.enable_ai_generation),
        )
        total_cost = Decimal(str(base["estimated_total_usd"]))

        result = dict(base)
        result["budget_limit_usd"] = float(job.budget_limit_usd) if job.budget_limit_usd else None
        result["within_budget"] = job.budget_limit_usd is None or total_cost <= job.budget_limit_usd

        # Include actual vs estimated comparison when actual data exists
        actual = float(job.actual_cost_usd) if job.actual_cost_usd else 0
        if actual > 0:
            variance = actual - float(total_cost)
            estimated_f = float(total_cost)
            result["actual_cost_usd"] = actual
            result["cost_variance_usd"] = round(variance, 4)
            result["cost_variance_pct"] = round(variance / estimated_f * 100, 1) if estimated_f > 0 else None
            result["over_estimate"] = variance > estimated_f * 0.2 if estimated_f > 0 else False

        return result

    def start_job(self, job_id: int, user_id: int) -> BatchImportJob:
        """
        Start processing a job after user confirms budget.

        Args:
            job_id: ID of the job to start
            user_id: ID of the user starting the job

        Returns:
            Updated job instance
        """
        job = BatchImportJob.query.get_or_404(job_id)

        # Verify ownership
        if job.user_id != user_id:
            raise PermissionError("You don't have permission to start this job")

        # Verify status
        if job.status not in [BatchJobStatus.AWAITING_CONFIRMATION, BatchJobStatus.PAUSED]:
            raise ValueError(f"Cannot start job in status: {job.status.value}")

        # Update job status
        job.status = BatchJobStatus.PROCESSING
        job.started_at = job.started_at or datetime.utcnow()

        db.session.commit()

        logger.info(f"Started batch import job {job.job_uuid}")

        return job

    def pause_job(self, job_id: int, user_id: int) -> BatchImportJob:
        """Pause a running job."""
        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != user_id:
            raise PermissionError("You don't have permission to pause this job")

        if job.status != BatchJobStatus.PROCESSING:
            raise ValueError(f"Cannot pause job in status: {job.status.value}")

        job.status = BatchJobStatus.PAUSED

        # Pause any processing batches
        for batch in job.batches:
            if batch.status == BatchStatus.PROCESSING:
                batch.status = BatchStatus.QUEUED

        db.session.commit()

        logger.info(f"Paused batch import job {job.job_uuid}")

        return job

    def resume_job(self, job_id: int, user_id: int) -> BatchImportJob:
        """Resume a paused job."""
        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != user_id:
            raise PermissionError("You don't have permission to resume this job")

        if job.status != BatchJobStatus.PAUSED:
            raise ValueError(f"Cannot resume job in status: {job.status.value}")

        job.status = BatchJobStatus.PROCESSING

        db.session.commit()

        logger.info(f"Resumed batch import job {job.job_uuid}")

        return job

    def cancel_job(self, job_id: int, user_id: int, delete_staged: bool = True) -> BatchImportJob:
        """
        Cancel a job and optionally delete staged elements.

        Args:
            job_id: ID of the job to cancel
            user_id: ID of the user cancelling
            delete_staged: Whether to delete staged but uncommitted elements
        """
        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != user_id:
            raise PermissionError("You don't have permission to cancel this job")

        if job.status in [BatchJobStatus.COMPLETED, BatchJobStatus.CANCELLED]:
            raise ValueError(f"Cannot cancel job in status: {job.status.value}")

        job.status = BatchJobStatus.CANCELLED

        if delete_staged:
            # Collect uncommitted batch IDs and delete all elements in a single query
            uncommitted_batch_ids = [
                batch.id for batch in job.batches
                if batch.status != BatchStatus.COMMITTED
            ]
            if uncommitted_batch_ids:
                BatchImportElement.query.filter(
                    BatchImportElement.batch_id.in_(uncommitted_batch_ids),
                    BatchImportElement.is_committed.is_(False)
                ).delete(synchronize_session="fetch")
                for batch in job.batches:
                    if batch.id in uncommitted_batch_ids:
                        batch.status = BatchStatus.SKIPPED

        # Clean up uploaded file
        if job.file_path and os.path.exists(job.file_path):
            try:
                os.remove(job.file_path)
                logger.info("Deleted upload file on cancel: %s", job.file_path)
            except Exception as e:
                logger.warning("Failed to delete file on cancel %s: %s", job.file_path, e)

        db.session.commit()

        logger.info(f"Cancelled batch import job {job.job_uuid}")

        return job

    def delete_job(self, job_id: int, user_id: int) -> None:
        """Delete a job and all associated data."""
        job = BatchImportJob.query.get_or_404(job_id)

        if job.user_id != user_id:
            raise PermissionError("You don't have permission to delete this job")

        # Delete file if exists
        if job.file_path and os.path.exists(job.file_path):
            try:
                os.remove(job.file_path)
            except Exception as e:
                logger.warning(f"Failed to delete file {job.file_path}: {e}")

        # Delete job (cascade deletes related records)
        db.session.delete(job)
        db.session.commit()

        logger.info(f"Deleted batch import job {job.job_uuid}")

    def get_job_progress(self, job_id: int) -> Dict[str, Any]:
        """
        Get real-time progress information for a job.

        Returns detailed progress for UI display.
        """
        job = BatchImportJob.query.get_or_404(job_id)

        # Count batch statuses
        batch_counts = {
            "queued": 0,
            "processing": 0,
            "ready_for_review": 0,
            "approved": 0,
            "committed": 0,
            "rejected": 0,
            "failed": 0,
        }
        current_batch = None

        for batch in job.batches:
            status_key = batch.status.value if batch.status else "queued"
            if status_key in batch_counts:
                batch_counts[status_key] += 1
            if batch.status == BatchStatus.PROCESSING:
                current_batch = batch.to_dict()

        # Calculate totals (treat None as 0 for legacy/migration data)
        total_elements = sum((b.total_elements_generated or 0) for b in job.batches)
        total_approved = sum((b.elements_approved or 0) for b in job.batches)
        total_rejected = sum((b.elements_rejected or 0) for b in job.batches)

        return {
            "job": job.to_dict(),
            "batch_counts": batch_counts,
            "current_batch": current_batch,
            "totals": {
                "applications_processed": sum((b.processed_applications or 0) for b in job.batches),
                "applications_total": job.total_applications,
                "elements_generated": total_elements,
                "elements_approved": total_approved,
                "elements_rejected": total_rejected,
                "elements_pending": total_elements - total_approved - total_rejected,
            },
            "cost": {
                "estimated": float(job.estimated_cost_usd) if job.estimated_cost_usd else 0,
                "actual": float(job.actual_cost_usd) if job.actual_cost_usd else 0,
                "budget": float(job.budget_limit_usd) if job.budget_limit_usd else None,
                "remaining": float(job.cost_remaining) if job.cost_remaining else None,
            },
        }

    def get_user_jobs(
        self,
        user_id: int,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[BatchImportJob], int]:
        """Get jobs for a user with optional filtering."""
        query = BatchImportJob.query.filter_by(user_id=user_id)

        if status_filter:
            try:
                status_enum = BatchJobStatus(status_filter)
                query = query.filter_by(status=status_enum)
            except ValueError:
                logger.warning(
                    "Dropped invalid status filter '%s' — not a valid BatchJobStatus",
                    status_filter,
                )

        total = query.count()
        jobs = query.order_by(BatchImportJob.created_at.desc()).offset(offset).limit(limit).all()

        return jobs, total

    def check_and_complete_job(self, job_id: int) -> BatchImportJob:
        """
        Check if all batches are done and update job status.

        Called after batch approval/commit to check if job is complete.
        """
        job = BatchImportJob.query.get_or_404(job_id)

        # Count completed batches (committed, rejected, or skipped)
        terminal_statuses = [BatchStatus.COMMITTED, BatchStatus.REJECTED, BatchStatus.SKIPPED]
        completed_batches = sum(1 for b in job.batches if b.status in terminal_statuses)

        job.batches_completed = completed_batches
        job.batches_committed = sum(1 for b in job.batches if b.status == BatchStatus.COMMITTED)
        job.batches_rejected = sum(1 for b in job.batches if b.status == BatchStatus.REJECTED)

        # Check if all batches are done
        if completed_batches >= job.total_batches:
            if job.status == BatchJobStatus.PROCESSING:
                job.status = BatchJobStatus.COMPLETED
                job.completed_at = datetime.utcnow()
                logger.info(f"Batch import job {job.job_uuid} completed")
            elif job.status in [
                BatchJobStatus.COMPLETED,
                BatchJobStatus.CANCELLED,
                BatchJobStatus.FAILED,
            ]:
                logger.debug(
                    "Job %s already terminal (%s); skipping completion transition",
                    job.job_uuid,
                    job.status.value,
                )
            else:
                logger.warning(
                    "All batches are terminal but job %s is in %s; not forcing completion",
                    job.job_uuid,
                    job.status.value if job.status else "unknown",
                )

        db.session.commit()

        return job

    def analyze_file(
        self,
        file: FileStorage,
        user_id: int,
        archimate_mode: str = "standard",
        enable_ai_generation: bool = True,
    ) -> Dict[str, Any]:
        """
        Analyze import file before creating job.

        Returns analysis with:
        - File stats (rows, columns, format)
        - Duplicate detection (in-file and vs database)
        - Data quality issues
        - Cost estimation
        - Column mapping suggestions
        """
        # Save temp file for analysis
        temp_path, file_hash = self._save_file(file)

        try:
            # Parse applications
            applications = self._parse_file(temp_path)
            total_apps = len(applications)

            if total_apps == 0:
                raise ValueError("No applications found in file")

            # Get columns from first application
            columns = list(applications[0].keys()) if applications else []

            # Analyze duplicates
            seen_names = set()
            in_file_duplicates = []
            no_name_count = 0

            for idx, app in enumerate(applications, start=1):
                name = app.get("name", "").strip()
                if not name:
                    no_name_count += 1
                    continue

                name_lower = name.lower()
                if name_lower in seen_names:
                    in_file_duplicates.append({"row": idx, "name": name})
                seen_names.add(name_lower)

            # Check database duplicates (case-insensitive, shared detector)
            from app.modules.import_batch.v2.services.unified_import.duplicate_detector_v2 import (
                DuplicateDetector,
            )

            lookup = DuplicateDetector.preload_existing_apps()  # model-safety-ok: prefetched
            db_duplicates = []
            for name_lower in seen_names:
                match = lookup["by_name"].get(name_lower)
                if match:
                    db_duplicates.append({"id": match["id"], "name": match["name"]})

            unique_apps = total_apps - len(in_file_duplicates) - no_name_count
            will_create = unique_apps - len(db_duplicates)
            will_update = len(db_duplicates)

            # Estimate costs
            cost_estimate = self._estimate_cost_for_count(
                app_count=will_create, mode=archimate_mode, enable_ai=enable_ai_generation
            )

            # Expected elements per application
            mode_multipliers = {
                "quick": {"min": 3, "max": 5},  # App + 2 - 4 basic elements
                "standard": {"min": 8, "max": 12},  # Multi-layer
                "comprehensive": {"min": 15, "max": 25},  # Full architecture
            }

            multiplier = mode_multipliers.get(archimate_mode, {"min": 8, "max": 12})
            expected_elements_min = will_create * multiplier["min"]
            expected_elements_max = will_create * multiplier["max"]

            analysis = {
                "file_stats": {
                    "filename": secure_filename(file.filename) or "upload",
                    "format": temp_path.split(".")[-1].upper(),
                    "total_rows": total_apps,
                    "columns": columns,
                    "column_count": len(columns),
                },
                "duplicate_analysis": {
                    "in_file_duplicates": len(in_file_duplicates),
                    "in_file_duplicate_details": in_file_duplicates[:10],  # First 10
                    "database_duplicates": len(db_duplicates),
                    "database_duplicate_details": db_duplicates[:10],
                    "missing_names": no_name_count,
                },
                "import_summary": {
                    "total_in_file": total_apps,
                    "unique_applications": unique_apps,
                    "will_create": will_create,
                    "will_update": will_update,
                    "will_skip": len(in_file_duplicates) + no_name_count,
                },
                "quality_issues": {
                    "missing_names": no_name_count,
                    "has_duplicates": len(in_file_duplicates) > 0,
                },
                "cost_estimate": cost_estimate,
                "expected_output": {
                    "archimate_mode": archimate_mode,
                    "enable_ai_generation": enable_ai_generation,
                    "expected_elements_min": expected_elements_min,
                    "expected_elements_max": expected_elements_max,
                    "elements_per_app": f"{multiplier['min']}-{multiplier['max']}",
                },
            }

            return analysis

        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to delete temp file {temp_path}: {e}")

    def _estimate_cost_for_count(
        self, app_count: int, mode: str = "standard", enable_ai: bool = True
    ) -> Dict[str, Any]:
        """The one estimator: used by job creation, the analyse step and the new-import page."""
        mode_multiplier = self.ARCHIMATE_MODE_MULTIPLIERS.get(mode, Decimal("2.5"))

        if enable_ai:
            archimate_cost = self.cost_per_app_base * mode_multiplier * app_count
            capability_cost = self.cost_capability_mapping * app_count
            process_cost = self.cost_process_classification * app_count
        else:
            archimate_cost = Decimal("0")
            capability_cost = Decimal("0")
            process_cost = Decimal("0")

        total_cost = archimate_cost + capability_cost + process_cost

        return {
            "total_applications": app_count,
            "archimate_mode": mode,
            "enable_ai_generation": enable_ai,
            "estimated_total_usd": float(total_cost),
            "cost_per_application": float(total_cost / app_count) if app_count > 0 else 0,
            "breakdown": {
                "archimate_generation": float(archimate_cost),
                "capability_mapping": float(capability_cost),
                "process_classification": float(process_cost),
            },
        }


def format_estimate_money(amount) -> str:
    """Format an estimate in the signed-in organisation's currency, the way every
    server-rendered money figure is formatted."""
    from app.middleware.tenant_context import current_org
    from app.services.currency_service import CurrencyService
    from config import CurrencyConfig

    try:
        code = CurrencyConfig.get_org_currency_code(current_org())
    except Exception:  # noqa: BLE001 - a figure must still render
        code = None
    return CurrencyService().format_currency(float(amount or 0), code)
