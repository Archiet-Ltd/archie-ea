"""
Import Orchestrator

Smart routing between Quick Mode (modal, direct commit) and
Governed Mode (dashboard, approval workflow) for application imports.
"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.modules.import_batch.v2.services.unified_import.cost_estimator_v2 import CostEstimate, CostEstimator
from app.modules.import_batch.v2.services.unified_import.duplicate_detector_v2 import (
    DuplicateAnalysisResult,
    DuplicateDetector,
)
from app.modules.import_batch.v2.services.unified_import.file_parser_v2 import FileParser, FileStats
from app.services.application_cost_accessor import (
    apply_cost_to_application,
    detect_cost_columns,
    get_reporting_currency,
    map_import_cost_columns,
)

if TYPE_CHECKING:
    from app.models.application_portfolio import ApplicationComponent
    from app.models.batch_import import BatchImportJob

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("import_audit")

# ---------------------------------------------------------------------------
# Import Idempotency Cache
# ---------------------------------------------------------------------------
# In-memory cache keyed by SHA-256(file_content + user_id).  Prevents the
# same file from being processed twice within IDEMPOTENCY_TTL seconds when a
# user accidentally double-clicks or retries an import.
# ---------------------------------------------------------------------------

_import_idempotency_cache: Dict[str, Dict[str, Any]] = {}
_idempotency_lock = threading.Lock()
IDEMPOTENCY_TTL = 300  # fabricated-ok: config constant (idempotency window seconds), not measured data


def _make_idempotency_key(file_content: bytes, user_id: int) -> str:
    """Generate a SHA-256 idempotency key from file content and user ID."""
    return hashlib.sha256(file_content + str(user_id).encode("utf-8")).hexdigest()


def _cleanup_expired_entries() -> None:
    """Remove expired entries from the idempotency cache.  Caller must hold _idempotency_lock."""
    now = time.time()
    expired = [k for k, v in _import_idempotency_cache.items() if now - v["time"] > IDEMPOTENCY_TTL]
    for k in expired:
        del _import_idempotency_cache[k]


def check_import_idempotency(file_content: bytes, user_id: int) -> Optional[Dict[str, Any]]:
    """Check whether an import with the same content + user was recently completed.

    Args:
        file_content: Raw bytes of the uploaded file.
        user_id: ID of the authenticated user performing the import.

    Returns:
        The cached result dict if a duplicate is detected, or ``None`` if
        this is a fresh import that should proceed.
    """
    key = _make_idempotency_key(file_content, user_id)
    with _idempotency_lock:
        _cleanup_expired_entries()
        entry = _import_idempotency_cache.get(key)
        if entry is not None:
            logger.info("Import idempotency hit for user %s (key=%s...)", user_id, key[:12])
            return entry["result"]
    return None


def store_import_idempotency(file_content: bytes, user_id: int, result: Dict[str, Any]) -> None:
    """Store the result of a successful import in the idempotency cache.

    Args:
        file_content: Raw bytes of the uploaded file.
        user_id: ID of the authenticated user performing the import.
        result: Serialisable dict summarising the import outcome.
    """
    key = _make_idempotency_key(file_content, user_id)
    with _idempotency_lock:
        _cleanup_expired_entries()
        _import_idempotency_cache[key] = {"result": result, "time": time.time()}
    logger.info("Import idempotency stored for user %s (key=%s...)", user_id, key[:12])


class ImportMode(Enum):
    """Available import modes."""

    QUICK = "quick"
    GOVERNED = "governed"


@dataclass
class ImportAnalysisResult:
    """Result of analyzing an import file."""

    file_stats: FileStats
    duplicate_analysis: DuplicateAnalysisResult
    cost_estimate: CostEstimate
    recommended_mode: ImportMode
    mode_reason: str
    can_use_quick_mode: bool
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "file_stats": self.file_stats.to_dict(),
            "duplicate_analysis": self.duplicate_analysis.to_dict(),
            "cost_estimate": self.cost_estimate.to_dict(),
            "recommended_mode": self.recommended_mode.value,
            "mode_reason": self.mode_reason,
            "can_use_quick_mode": self.can_use_quick_mode,
            "warnings": self.warnings,
        }


@dataclass
class QuickImportResult:
    """Result of a quick mode import."""

    success: bool
    applications_created: int
    applications_updated: int
    applications_skipped: int
    elements_generated: int
    total_cost_usd: Decimal
    processing_time_seconds: float
    errors: List[str] = field(default_factory=list)
    created_app_ids: List[int] = field(default_factory=list)
    conflict_resolution: str = "merge"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "success": self.success,
            "applications_created": self.applications_created,
            "applications_updated": self.applications_updated,
            "applications_skipped": self.applications_skipped,
            "elements_generated": self.elements_generated,
            "total_cost_usd": float(self.total_cost_usd),
            "processing_time_seconds": self.processing_time_seconds,
            "errors": self.errors,
            "created_app_ids": self.created_app_ids,
            "conflict_resolution": self.conflict_resolution,
        }


class ImportOrchestrator:
    """
    Smart routing between Quick and Governed import modes.

    Quick Mode (< threshold rows):
    - Modal-based UI
    - Direct database commit
    - No approval workflow
    - Immediate results

    Governed Mode (>= threshold rows):
    - Dashboard-based UI
    - Batch processing with checkpoints
    - Element-level approval workflow
    - Budget enforcement
    - Full audit trail
    """

    # Default threshold for quick mode eligibility
    QUICK_MODE_THRESHOLD = 50

    # Maximum rows allowed in quick mode (hard limit)
    QUICK_MODE_MAX_ROWS = 100

    # Cost threshold - above this, recommend governed mode
    COST_THRESHOLD_USD = Decimal("5.00")

    def __init__(
        self,
        quick_mode_threshold: int = None,
        cost_threshold_usd: Decimal = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            quick_mode_threshold: Override default row threshold for quick mode
            cost_threshold_usd: Override default cost threshold for governed mode
        """
        self.quick_mode_threshold = quick_mode_threshold or self.QUICK_MODE_THRESHOLD
        self.cost_threshold_usd = cost_threshold_usd or self.COST_THRESHOLD_USD

        self.file_parser = FileParser()
        self.duplicate_detector = None  # Initialized per-file with name column
        self.cost_estimator = CostEstimator()

    def analyze_import(
        self,
        file_storage,
        user_id: int,
        archimate_mode: str = "standard",
        enable_ai: bool = True,
        budget_limit_usd: float = None,
        check_database_duplicates: bool = True,
    ) -> ImportAnalysisResult:
        """
        Analyze an import file and recommend the appropriate mode.

        Args:
            file_storage: FileStorage object from Flask request
            user_id: ID of the user performing the import
            archimate_mode: ArchiMate generation mode (quick/standard/comprehensive)
            enable_ai: Whether AI generation is enabled
            budget_limit_usd: Optional budget limit
            check_database_duplicates: Whether to check for existing apps in DB

        Returns:
            ImportAnalysisResult with recommendation
        """
        logger.info(f"Analyzing import file: {file_storage.filename} for user {user_id}")

        # Parse file and get stats
        columns, rows = self.file_parser.parse_file_stream(file_storage)

        # Create FileStats manually from parsed data
        file_stats = FileStats(
            filename=file_storage.filename,
            format=file_storage.filename.split(".")[-1].lower(),
            total_rows=len(rows),
            columns=columns,
            column_count=len(columns),
        )

        # Initialize duplicate detector with detected name column
        name_column = self.file_parser.find_name_column(file_stats.columns)
        self.duplicate_detector = DuplicateDetector(name_column=name_column)

        # Analyze duplicates
        duplicate_analysis = self.duplicate_detector.analyze_duplicates(
            rows, check_database=check_database_duplicates
        )

        # Estimate costs
        apps_to_process = duplicate_analysis.will_create + duplicate_analysis.will_update
        cost_estimate = self.cost_estimator.estimate_cost(
            app_count=apps_to_process,
            mode=archimate_mode,
            enable_ai=enable_ai,
            budget_limit_usd=budget_limit_usd,
        )

        # Determine recommended mode
        recommended_mode, mode_reason, can_use_quick = self._determine_mode(
            file_stats=file_stats,
            duplicate_analysis=duplicate_analysis,
            cost_estimate=cost_estimate,
            budget_limit_usd=budget_limit_usd,
        )

        # Collect warnings
        warnings = self._collect_warnings(
            file_stats=file_stats,
            duplicate_analysis=duplicate_analysis,
            cost_estimate=cost_estimate,
        )

        result = ImportAnalysisResult(
            file_stats=file_stats,
            duplicate_analysis=duplicate_analysis,
            cost_estimate=cost_estimate,
            recommended_mode=recommended_mode,
            mode_reason=mode_reason,
            can_use_quick_mode=can_use_quick,
            warnings=warnings,
        )

        logger.info(
            f"Analysis complete: {file_stats.total_rows} rows, "
            f"recommended mode: {recommended_mode.value}, "
            f"estimated cost: ${float(cost_estimate.estimated_total_usd):.2f}"
        )

        audit_logger.info(
            "IMPORT_VALIDATION_COMPLETE | user=%s | filename=%s | file_type=%s | "
            "total_rows=%d | duplicates_in_file=%d | duplicates_in_db=%d | "
            "will_create=%d | will_update=%d | missing_names=%d | "
            "recommended_mode=%s | estimated_cost=%.2f | warnings=%d",
            user_id,
            file_storage.filename,
            file_stats.format,
            file_stats.total_rows,
            duplicate_analysis.in_file_count,
            duplicate_analysis.database_count,
            duplicate_analysis.will_create,
            duplicate_analysis.will_update,
            duplicate_analysis.missing_names_count,
            recommended_mode.value,
            float(cost_estimate.estimated_total_usd),
            len(warnings),
        )

        return result

    def _determine_mode(
        self,
        file_stats: FileStats,
        duplicate_analysis: DuplicateAnalysisResult,
        cost_estimate: CostEstimate,
        budget_limit_usd: float = None,
    ) -> Tuple[ImportMode, str, bool]:
        """
        Determine the recommended import mode.

        Returns:
            Tuple of (recommended_mode, reason_string, can_use_quick_mode)
        """
        row_count = file_stats.total_rows
        estimated_cost = Decimal(str(cost_estimate.estimated_total_usd))

        # Hard limit - cannot use quick mode above max
        if row_count > self.QUICK_MODE_MAX_ROWS:
            return (
                ImportMode.GOVERNED,
                f"File has {row_count} rows, exceeding quick mode limit of {self.QUICK_MODE_MAX_ROWS}",
                False,
            )

        # Check if budget requires governance
        if budget_limit_usd and estimated_cost > Decimal(str(budget_limit_usd)):
            return (
                ImportMode.GOVERNED,
                f"Estimated cost (${float(estimated_cost):.2f}) exceeds budget (${budget_limit_usd:.2f})",
                False,
            )

        # Check cost threshold
        if estimated_cost > self.cost_threshold_usd:
            return (
                ImportMode.GOVERNED,
                f"Estimated cost (${float(estimated_cost):.2f}) exceeds threshold for quick mode",
                True,  # Can still use quick mode if user chooses
            )

        # Check row threshold (soft limit)
        if row_count > self.quick_mode_threshold:
            return (
                ImportMode.GOVERNED,
                f"File has {row_count} rows, recommend governed mode for better tracking",
                True,  # Can still use quick mode if user chooses
            )

        # Below all thresholds - recommend quick mode
        return (
            ImportMode.QUICK,
            f"File has {row_count} rows, suitable for quick import",
            True,
        )

    def _collect_warnings(
        self,
        file_stats: FileStats,
        duplicate_analysis: DuplicateAnalysisResult,
        cost_estimate: CostEstimate,
    ) -> List[str]:
        """Collect warnings about the import."""
        warnings = []

        # Duplicate warnings
        if duplicate_analysis.in_file_count > 0:
            warnings.append(
                f"{duplicate_analysis.in_file_count} duplicate names found in file (will be skipped)"
            )

        if duplicate_analysis.database_count > 0:
            warnings.append(
                f"{duplicate_analysis.database_count} applications already exist in database (will be updated)"
            )

        if duplicate_analysis.missing_names_count > 0:
            warnings.append(
                f"{duplicate_analysis.missing_names_count} rows have missing names (will be skipped)"
            )

        # Cost warnings
        if cost_estimate.estimated_total_usd > 10.0:
            warnings.append(f"High estimated cost: ${cost_estimate.estimated_total_usd:.2f}")

        if not cost_estimate.within_budget:
            warnings.append("Estimated cost exceeds budget limit")

        return warnings

    def start_quick_import(
        self,
        file_storage,
        user_id: int,
        archimate_mode: str = "standard",
        enable_ai: bool = True,
        update_existing: bool = True,
        conflict_resolution: str = None,
    ) -> QuickImportResult:
        """
        Execute a quick mode import (direct commit, no approval).

        Args:
            file_storage: FileStorage object from Flask request
            user_id: ID of the user performing the import
            archimate_mode: ArchiMate generation mode
            enable_ai: Whether to generate AI elements
            update_existing: Whether to update existing applications (deprecated, use conflict_resolution)
            conflict_resolution: How to handle existing applications.
                "skip" - leave existing app unchanged
                "overwrite" - replace all fields with import data
                "merge" - only update fields that are non-empty in import data (default)

        Returns:
            QuickImportResult with import statistics
        """
        # Resolve conflict_resolution: explicit parameter takes precedence,
        # fall back to update_existing for backward compatibility.
        if conflict_resolution is None:
            conflict_resolution = "merge" if update_existing else "skip"
        conflict_resolution = conflict_resolution.lower()
        if conflict_resolution not in ("skip", "overwrite", "merge"):
            logger.warning(
                "Invalid conflict_resolution '%s', defaulting to 'merge'",
                conflict_resolution,
            )
            conflict_resolution = "merge"

        logger.info(
            "Quick import conflict_resolution=%s for file %s",
            conflict_resolution, file_storage.filename,
        )
        import time

        from app import db

        start_time = time.time()
        logger.info(f"Starting quick import: {file_storage.filename} for user {user_id}")

        # Parse file
        columns, rows = self.file_parser.parse_file_stream(file_storage)

        # Create FileStats manually from parsed data
        file_stats = FileStats(
            filename=file_storage.filename,
            format=file_storage.filename.split(".")[-1].lower(),
            total_rows=len(rows),
            columns=columns,
            column_count=len(columns),
        )

        audit_logger.info(
            "IMPORT_STARTED | user=%s | filename=%s | file_type=%s | "
            "row_count=%d | mode=quick | ai_enabled=%s | archimate_mode=%s",
            user_id,
            file_storage.filename,
            file_stats.format,
            len(rows),
            enable_ai,
            archimate_mode,
        )

        # Verify quick mode eligibility
        if file_stats.total_rows > self.QUICK_MODE_MAX_ROWS:
            return QuickImportResult(
                success=False,
                applications_created=0,
                applications_updated=0,
                applications_skipped=0,
                elements_generated=0,
                total_cost_usd=Decimal("0"),
                processing_time_seconds=time.time() - start_time,
                errors=[
                    f"File has {file_stats.total_rows} rows, exceeding quick mode limit of {self.QUICK_MODE_MAX_ROWS}"
                ],
            )

        # Initialize services
        name_column = self.file_parser.find_name_column(file_stats.columns)
        self.duplicate_detector = DuplicateDetector(name_column=name_column)

        # Process applications
        created_count = 0
        updated_count = 0
        skipped_count = 0
        elements_count = 0
        total_cost = Decimal("0")
        errors = []
        created_ids = []

        try:
            from app.models.application_portfolio import ApplicationComponent
            from app.modules.import_batch.v2.services.unified_import.ai_element_generator_v2 import AIElementGenerator

            ai_generator = AIElementGenerator() if enable_ai else None

            seen_names = set()

            # Batch-load all existing application names to avoid N+1 queries in the loop
            all_apps = ApplicationComponent.query.all()
            app_name_lookup = {a.name.lower(): a for a in all_apps if a.name}

            for row in rows:
                app_name = row.get(name_column, "").strip() if row.get(name_column) else ""

                # Skip empty names
                if not app_name:
                    skipped_count += 1
                    continue

                # Skip in-file duplicates
                name_lower = app_name.lower()
                if name_lower in seen_names:
                    skipped_count += 1
                    continue
                seen_names.add(name_lower)

                # Use a savepoint per row so that one row failure does not
                # corrupt the session or prevent other rows from succeeding
                row_savepoint = db.session.begin_nested()
                try:
                    # Check if application exists using pre-loaded lookup
                    existing_app = app_name_lookup.get(name_lower)

                    if existing_app:
                        if conflict_resolution == "skip":
                            logger.debug(
                                "Skipping existing app '%s' (id=%s) -- conflict_resolution=skip",
                                app_name, existing_app.id,
                            )
                            row_savepoint.rollback()
                            skipped_count += 1
                        elif conflict_resolution in ("overwrite", "merge"):
                            # Update existing application using the chosen mode
                            self._update_application(
                                existing_app, row, file_stats.columns,
                                mode=conflict_resolution,
                            )
                            logger.info(
                                "Updated existing app '%s' (id=%s) -- conflict_resolution=%s",
                                app_name, existing_app.id, conflict_resolution,
                            )
                            updated_count += 1

                            # Generate elements if AI enabled
                            if ai_generator:
                                elements, metrics = ai_generator.generate_elements_for_batch_app(
                                    app_context=self._build_app_context(row, file_stats.columns),
                                    mode=archimate_mode,
                                    use_real_ai=enable_ai,
                                )
                                elements_count += len(elements)
                                total_cost += metrics.cost_usd
                                # Store elements on the application
                                self._store_elements(existing_app, elements)

                            row_savepoint.commit()
                        else:
                            row_savepoint.rollback()
                            skipped_count += 1
                    else:
                        # Create new application
                        new_app = self._create_application(row, file_stats.columns, user_id)
                        db.session.add(new_app)
                        db.session.flush()  # Get the ID
                        created_ids.append(new_app.id)
                        # Update lookup so subsequent iterations find this application
                        app_name_lookup[name_lower] = new_app
                        created_count += 1

                        # Generate elements if AI enabled
                        if ai_generator:
                            elements, metrics = ai_generator.generate_elements_for_batch_app(
                                app_context=self._build_app_context(row, file_stats.columns),
                                mode=archimate_mode,
                                use_real_ai=enable_ai,
                            )
                            elements_count += len(elements)
                            total_cost += metrics.cost_usd
                            # Store elements on the application
                            self._store_elements(new_app, elements)

                        row_savepoint.commit()

                except Exception as e:
                    row_savepoint.rollback()
                    logger.error(f"Error processing application '{app_name}': {e}")
                    errors.append(f"Error processing '{app_name}': {str(e)}")
                    skipped_count += 1

            # Commit all changes
            db.session.commit()

            logger.info(
                f"Quick import complete: {created_count} created, "
                f"{updated_count} updated, {skipped_count} skipped, "
                f"{elements_count} elements generated"
            )

            audit_logger.info(
                "IMPORT_COMMITTED | user=%s | filename=%s | mode=quick | "
                "created=%d | updated=%d | skipped=%d | elements=%d | "
                "errors=%d | elapsed=%.3fs",
                user_id,
                file_storage.filename,
                created_count,
                updated_count,
                skipped_count,
                elements_count,
                len(errors),
                time.time() - start_time,
            )

        except Exception as e:
            db.session.rollback()
            logger.error(f"Quick import failed: {e}", exc_info=True)

            audit_logger.error(
                "IMPORT_FAILED | user=%s | filename=%s | mode=quick | "
                "error=%s | elapsed=%.3fs",
                user_id,
                file_storage.filename,
                str(e),
                time.time() - start_time,
            )

            return QuickImportResult(
                success=False,
                applications_created=0,
                applications_updated=0,
                applications_skipped=file_stats.total_rows,
                elements_generated=0,
                total_cost_usd=Decimal("0"),
                processing_time_seconds=time.time() - start_time,
                errors=[f"Import failed: {str(e)}"],
                conflict_resolution=conflict_resolution,
            )

        return QuickImportResult(
            success=True,
            applications_created=created_count,
            applications_updated=updated_count,
            applications_skipped=skipped_count,
            elements_generated=elements_count,
            total_cost_usd=total_cost,
            processing_time_seconds=time.time() - start_time,
            errors=errors,
            created_app_ids=created_ids,
            conflict_resolution=conflict_resolution,
        )

    def start_governed_import(
        self,
        file_storage,
        user_id: int,
        job_name: str = None,
        archimate_mode: str = "standard",
        enable_ai: bool = True,
        budget_limit_usd: float = None,
        batch_size: int = 10,
    ) -> "BatchImportJob":
        """
        Start a governed mode import (creates batch job for dashboard).

        Args:
            file_storage: FileStorage object from Flask request
            user_id: ID of the user performing the import
            job_name: Optional name for the job
            archimate_mode: ArchiMate generation mode
            enable_ai: Whether to generate AI elements
            budget_limit_usd: Optional budget limit
            batch_size: Number of applications per batch

        Returns:
            BatchImportJob instance
        """
        from app.modules.import_batch.v2.services.batch_import_service_v2 import BatchImportService

        logger.info(f"Starting governed import: {file_storage.filename} for user {user_id}")

        # Use existing batch import service
        batch_service = BatchImportService()

        # Create the job using the correct parameter names
        job = batch_service.create_job(
            user_id=user_id,
            file=file_storage,
            batch_size=batch_size,
            archimate_mode=archimate_mode,
            enable_ai_generation=enable_ai,
            budget_limit_usd=budget_limit_usd,
        )

        logger.info(f"Created governed import job {job.id}: {job.job_name}")

        audit_logger.info(
            "IMPORT_STARTED | user=%s | filename=%s | mode=governed | "
            "job_id=%s | job_name=%s | batch_size=%d | ai_enabled=%s | archimate_mode=%s",
            user_id,
            file_storage.filename,
            job.id,
            job.job_name,
            batch_size,
            enable_ai,
            archimate_mode,
        )

        return job

    def _build_app_context(self, row: Dict, columns: List[str]) -> Dict[str, Any]:
        """Build application context from row data."""
        # Map common column variations to standard keys
        column_mappings = {
            "name": ["name", "application_name", "app_name", "Name"],
            "description": ["description", "desc", "Description", "application_description"],
            "type": ["type", "application_type", "app_type", "Type"],
            "vendor": ["vendor", "vendor_name", "Vendor"],
            "status": ["status", "lifecycle_status", "Status"],
            "criticality": ["criticality", "business_criticality", "Criticality"],
        }

        context = {}
        for key, variations in column_mappings.items():
            for var in variations:
                if var in row and row[var]:
                    context[key] = row[var]
                    break

        # Add all other columns as source_data
        context["source_data"] = {k: v for k, v in row.items() if v}

        return context

    def _extract_cost_mapping(self, columns: List[str]) -> Dict[str, str]:
        """
        Build a cost column mapping from the available columns.

        Delegates to the shared detect_cost_columns in the accessor module.
        """
        return detect_cost_columns(columns)

    def _create_application(
        self, row: Dict, columns: List[str], user_id: int
    ) -> "ApplicationComponent":
        """Create a new ApplicationComponent from row data."""
        from app.models.application_portfolio import ApplicationComponent

        context = self._build_app_context(row, columns)

        app = ApplicationComponent(
            name=context.get("name", "Unnamed Application"),
            description=context.get("description"),
            application_type=context.get("type"),
            vendor_name=context.get("vendor"),
            lifecycle_status=context.get("status", "active"),
            business_criticality=context.get("criticality"),
            created_by_id=user_id,
        )

        # Apply cost fields through the accessor
        cost_mapping = self._extract_cost_mapping(columns)
        if cost_mapping:
            parsed = map_import_cost_columns(row, cost_mapping, reporting_currency=get_reporting_currency())
            apply_cost_to_application(app, parsed["cost_fields"])

        return app

    def _update_application(
        self, app: "ApplicationComponent", row: Dict, columns: List[str],
        mode: str = "merge",
    ) -> None:
        """
        Update an existing ApplicationComponent from row data.

        Args:
            app: Existing ApplicationComponent to update
            row: Row data from the import file
            columns: Column names from the file
            mode: "merge" only updates non-empty fields (preserves existing data),
                  "overwrite" replaces all mapped fields with import data
        """
        context = self._build_app_context(row, columns)

        field_map = {
            "description": "description",
            "type": "application_type",
            "vendor": "vendor_name",
            "status": "lifecycle_status",
            "criticality": "business_criticality",
        }

        for context_key, attr_name in field_map.items():
            value = context.get(context_key)
            if mode == "overwrite":
                # Overwrite: set the field regardless, even if import value is empty/None
                setattr(app, attr_name, value)
            else:
                # Merge: only update if the import data has a non-empty value
                if value:
                    setattr(app, attr_name, value)

        # Apply cost fields through the accessor (only in overwrite mode or when cost is provided)
        cost_mapping = self._extract_cost_mapping(columns)
        if cost_mapping:
            parsed = map_import_cost_columns(row, cost_mapping, reporting_currency=get_reporting_currency())
            # In merge mode, only apply cost if the import has a value for it
            if mode == "overwrite" or parsed["cost_fields"]:
                apply_cost_to_application(app, parsed["cost_fields"])

    def _store_elements(self, app: "ApplicationComponent", elements: List[Dict[str, Any]]) -> None:
        """Store generated ArchiMate elements for an application."""
        # Elements are stored in the application's archimate_elements JSON field
        # or in a related table depending on the data model
        if hasattr(app, "archimate_elements"):  # model-safety-ok: backref from ArchiMateElement, may not exist on all app types
            existing = app.archimate_elements or []
            app.archimate_elements = existing + elements
        elif hasattr(app, "generated_elements"):  # model-safety-ok: field does not exist on ApplicationComponent
            existing = app.generated_elements or []
            app.generated_elements = existing + elements
        else:
            # Store in metadata or a generic field
            if not app.metadata:
                app.metadata = {}
            existing = app.metadata.get("archimate_elements", [])
            app.metadata["archimate_elements"] = existing + elements
