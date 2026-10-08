"""
-> app.modules.import_batch.services.import_service

Import Preview Service

Generates preview data for batch import jobs before processing.
Provides validation results, duplicate detection against existing records,
and impact analysis showing estimated element generation.

Uses unified duplicate detection from duplicate_detection_utils.py.
"""

import logging
from typing import Any, Dict, List

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.batch_import import BatchImportApplication, BatchImportJob
from app.modules.import_batch.v2.services.duplicate_detection_utils_v2 import (
    DuplicateDetectionUtils,
)
from app.services.application_cost_accessor import (
    detect_cost_columns,
    get_reporting_currency,
    map_import_cost_columns,
)

logger = logging.getLogger(__name__)

# Lazy import for ImportValidator (may have missing dependencies)
_ImportValidator = None


def _get_import_validator():
    """Lazy-load ImportValidator from the real validation service, returning None if unavailable."""
    global _ImportValidator
    if _ImportValidator is not None:
        return _ImportValidator
    try:
        from app.services.import_validation.import_validator import ImportValidator
        _ImportValidator = ImportValidator
    except ImportError as e:
        logger.warning(f"ImportValidator unavailable: {e}")
        _ImportValidator = False
    return _ImportValidator

# ArchiMate element estimates per application by mode
ARCHIMATE_ESTIMATES = {
    "quick": {
        "layers": ["application"],
        "elements_per_layer": 3,
        "total_per_app": 3,
    },
    "standard": {
        "layers": ["business", "application", "technology"],
        "elements_per_layer": 3,
        "total_per_app": 9,
    },
    "comprehensive": {
        "layers": [
            "motivation",
            "strategy",
            "business",
            "application",
            "technology",
            "implementation",
        ],
        "elements_per_layer": 4,
        "total_per_app": 24,
    },
}


class ImportPreviewService:
    """Generates preview data for batch import jobs."""

    def generate_preview(self, job_id: int) -> Dict[str, Any]:
        """
        Generate comprehensive preview data for a batch import job.

        Args:
            job_id: ID of the BatchImportJob to preview.

        Returns:
            Dict with validation, duplicates, cost mapping, and impact data.
        """
        job = BatchImportJob.query.get_or_404(job_id)

        # Collect source data from all applications across batches
        applications = (
            BatchImportApplication.query.join(BatchImportApplication.batch)
            .filter(
                BatchImportApplication.batch.has(job_id=job.id),
            )
            .order_by(BatchImportApplication.row_number)
            .all()
        )

        applications_data = []
        for app in applications:
            row = app.source_data or {}
            row["_import_row"] = app.row_number
            row["_app_name"] = app.application_name
            applications_data.append(row)

        # Run validation
        validation = self._run_validation(applications_data)

        # Detect duplicates against existing records
        duplicates = self._detect_duplicates(applications_data)

        # Analyze cost column mapping
        cost_mapping = self._analyze_cost_mapping(applications_data)

        # Build impact analysis
        impact = self._analyze_impact(
            job, validation, duplicates, len(applications_data)
        )

        return {
            "job_id": job.id,
            "total_applications": len(applications_data),
            "validation": validation,
            "duplicates": duplicates,
            "cost_mapping": cost_mapping,
            "impact": impact,
        }

    def _run_validation(self, applications_data: List[Dict]) -> Dict[str, Any]:
        """
        Run import validation on application source data.

        Uses the existing ImportValidator in lenient mode to validate
        all rows and return structured results.
        """
        if not applications_data:
            return {
                "valid": True,
                "mode": "lenient",
                "summary": {
                    "total_rows": 0,
                    "valid_rows": 0,
                    "invalid_rows": 0,
                    "rows_with_warnings": 0,
                    "total_errors": 0,
                    "total_warnings": 0,
                },
                "row_details": [],
                "errors_by_field": {},
                "warnings_by_field": {},
            }

        try:
            # Strip internal metadata before validation
            clean_rows = []
            for row in applications_data:
                clean = {k: v for k, v in row.items() if not k.startswith("_")}
                clean_rows.append(clean)

            ImportValidator = _get_import_validator()
            if ImportValidator:
                validator = ImportValidator(mode="lenient")
                result = validator.validate(clean_rows)
                return result.to_dict()
            else:
                # Validator unavailable - report as invalid so the UI
                # shows an error rather than a false "all valid" state
                return {
                    "valid": False,
                    "mode": "lenient",
                    "summary": {
                        "total_rows": len(applications_data),
                        "valid_rows": 0,
                        "invalid_rows": len(applications_data),
                        "rows_with_warnings": 0,
                        "total_errors": 1,
                        "total_warnings": 0,
                    },
                    "row_details": [],
                    "errors_by_field": {},
                    "warnings_by_field": {},
                    "error": "Validation engine unavailable. Check system dependencies.",
                }
        except Exception as e:
            logger.error(f"Validation failed during preview: {e}", exc_info=True)
            return {
                "valid": False,
                "mode": "lenient",
                "summary": {
                    "total_rows": len(applications_data),
                    "valid_rows": 0,
                    "invalid_rows": 0,
                    "rows_with_warnings": 0,
                    "total_errors": 1,
                    "total_warnings": 0,
                },
                "row_details": [],
                "errors_by_field": {},
                "warnings_by_field": {},
                "error": str(e),
            }

    def _detect_duplicates(
        self,
        applications_data: List[Dict],
        threshold: float = 0.6,
    ) -> List[Dict[str, Any]]:
        """
        Detect potential duplicate matches between import rows and existing records.

        Uses unified duplicate detection from DuplicateDetectionUtils.
        Compares application names using configurable similarity algorithm.

        Args:
            applications_data: List of import row dicts with _app_name metadata.
            threshold: Minimum similarity score to report as a match (0.0-1.0).

        Returns:
            List of conflict dicts with import info and matching existing records.
        """
        if not applications_data:
            return []

        try:
            existing_apps = db.session.query(
                ApplicationComponent.id,
                ApplicationComponent.name,
                ApplicationComponent.description,
                ApplicationComponent.lifecycle_status,
            ).all()

            if not existing_apps:
                return []

            existing_names = [app.name for app in existing_apps if app.name]
            existing_descriptions = {
                app.name: app.description for app in existing_apps if app.name
            }

            conflicts = []
            for row in applications_data:
                import_name = row.get("_app_name", "")
                if not import_name:
                    continue

                matches = DuplicateDetectionUtils.find_all_database_matches(
                    import_name=import_name,
                    existing_names=existing_names,
                    mode="fuzzy",
                    threshold=threshold,
                )

                if matches:
                    formatted_matches = []
                    for match in matches:
                        app_id = None
                        app_status = None
                        for app in existing_apps:
                            if app.name == match.matched_name:
                                app_id = app.id
                                app_status = app.lifecycle_status
                                break

                        app_desc = existing_descriptions.get(match.matched_name, "")
                        formatted_matches.append(
                            {
                                "app_id": app_id,
                                "app_name": match.matched_name,
                                "app_description": (app_desc or "")[:200],
                                "app_status": app_status,
                                "similarity": round(match.score, 2),
                                "name_similarity": round(match.score, 2),
                            }
                        )

                    formatted_matches.sort(key=lambda m: m["similarity"], reverse=True)
                    conflicts.append(
                        {
                            "import_row": row.get("_import_row", 0),
                            "import_name": import_name,
                            "import_description": str(
                                row.get("description", row.get("Description", ""))
                            ).strip()[:200],
                            "import_type": str(
                                row.get(
                                    "type",
                                    row.get("Type", row.get("application_type", "")),
                                )
                            ).strip(),
                            "matches": formatted_matches[:5],
                        }
                    )

            return conflicts

        except Exception as e:
            logger.error(
                f"Duplicate detection failed during preview: {e}", exc_info=True
            )
            return []

    def _analyze_impact(
        self,
        job: BatchImportJob,
        validation: Dict,
        duplicates: List[Dict],
        total_apps: int,
    ) -> Dict[str, Any]:
        """
        Analyze the impact of the import on the repository.

        Estimates what will be created based on the archimate mode,
        validation results, and duplicate conflicts.
        """
        mode = job.archimate_mode or "standard"
        estimates = ARCHIMATE_ESTIMATES.get(mode, ARCHIMATE_ESTIMATES["standard"])

        # Count applications by category
        duplicate_rows = {d["import_row"] for d in duplicates}
        invalid_rows = validation.get("summary", {}).get("invalid_rows", 0)

        new_applications = total_apps - len(duplicate_rows)
        if new_applications < 0:
            new_applications = 0

        # Estimate elements if AI generation is enabled
        if job.enable_ai_generation:
            elements_per_app = estimates["total_per_app"]
            total_estimated_elements = new_applications * elements_per_app

            # Break down by layer
            layer_breakdown = {}
            for layer in estimates["layers"]:
                layer_breakdown[layer] = (
                    new_applications * estimates["elements_per_layer"]
                )
        else:
            elements_per_app = 0
            total_estimated_elements = 0
            layer_breakdown = {}

        return {
            "total_applications": total_apps,
            "new_applications": new_applications,
            "potential_duplicates": len(duplicate_rows),
            "validation_errors": invalid_rows,
            "archimate_mode": mode,
            "ai_generation_enabled": job.enable_ai_generation,
            "estimated_elements": {
                "total": total_estimated_elements,
                "per_application": elements_per_app,
                "by_layer": layer_breakdown,
            },
            "estimated_cost_usd": float(job.estimated_cost_usd)
            if job.estimated_cost_usd
            else 0,
        }

    def _analyze_cost_mapping(self, applications_data: List[Dict]) -> Dict[str, Any]:
        """
        Analyze cost columns in the import data and provide typed preview.

        Returns a dict with:
        - detected_columns: list of cost column names found in the data
        - column_mapping: suggested mapping from file columns to cost fields
        - row_previews: per-row parsed cost values with warnings/errors
        - summary: aggregate statistics
        """
        if not applications_data:
            return {
                "detected_columns": [],
                "column_mapping": {},
                "row_previews": [],
                "summary": {
                    "total_rows": 0,
                    "rows_with_cost": 0,
                    "rows_with_errors": 0,
                    "rows_with_warnings": 0,
                },
            }

        # Get all column names from the first row (excluding internal metadata)
        first_row = applications_data[0]
        all_columns = [c for c in first_row.keys() if not c.startswith("_")]

        # Detect cost columns using the shared accessor function
        column_mapping = detect_cost_columns(all_columns)
        detected_columns = [v for v in column_mapping.values()]

        # Parse cost for each row using the detected mapping
        row_previews = []
        rows_with_cost = 0
        rows_with_errors = 0
        rows_with_warnings = 0

        for row in applications_data:
            import_row = row.get("_import_row", 0)
            app_name = row.get("_app_name", "")

            parsed = map_import_cost_columns(row, column_mapping, reporting_currency=get_reporting_currency())

            has_cost = bool(parsed["cost_fields"])
            has_errors = bool(parsed["cost_errors"])
            has_warnings = bool(parsed["cost_warnings"])

            if has_cost:
                rows_with_cost += 1
            if has_errors:
                rows_with_errors += 1
            if has_warnings:
                rows_with_warnings += 1

            row_previews.append({
                "import_row": import_row,
                "application_name": app_name,
                "cost_fields": {
                    k: float(v) if v is not None else None
                    for k, v in parsed["cost_fields"].items()
                },
                "cost_warnings": parsed["cost_warnings"],
                "cost_errors": parsed["cost_errors"],
            })

        return {
            "detected_columns": detected_columns,
            "column_mapping": column_mapping,
            "row_previews": row_previews,
            "summary": {
                "total_rows": len(applications_data),
                "rows_with_cost": rows_with_cost,
                "rows_with_errors": rows_with_errors,
                "rows_with_warnings": rows_with_warnings,
            },
        }

    def resolve_conflicts(
        self,
        job_id: int,
        resolutions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Save conflict resolution choices for a job.

        Args:
            job_id: ID of the BatchImportJob.
            resolutions: List of resolution dicts:
                {import_row: int, action: "create_new"|"skip"|"update", target_app_id: int|null}

        Returns:
            Summary of saved resolutions.
        """
        job = BatchImportJob.query.get_or_404(job_id)

        # Store resolutions in custom_field_mappings
        mappings = job.custom_field_mappings or {}
        mappings["conflict_resolutions"] = resolutions
        job.custom_field_mappings = mappings

        db.session.commit()

        # Build summary
        action_counts = {"create_new": 0, "skip": 0, "update": 0}
        for r in resolutions:
            action = r.get("action", "create_new")
            if action in action_counts:
                action_counts[action] += 1

        return {
            "total_resolved": len(resolutions),
            "actions": action_counts,
        }
