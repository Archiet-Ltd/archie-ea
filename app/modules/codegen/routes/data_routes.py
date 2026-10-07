"""Data Pipeline routes for the Code Workbench.

All routes live under /solutions/<id>/codegen/data/* and are registered on
``codegen_bp`` (defined in codegen_routes.py).  This module is imported at the
bottom of codegen_routes.py so Flask picks up the decorated handlers.

Design spec: docs/2026-03-30-archie-deploy-zero-dev-design.md §2
"""
import logging

from flask import jsonify, request
from flask_login import login_required

from app.extensions import db
from app.models.solution_models import Solution
from app.modules.codegen.models import CodegenGeneration, DataImport
from app.modules.codegen.routes.codegen_routes import codegen_bp
from app.modules.codegen.routes._helpers import _check_access, _extract_field_types, _extract_model_fields

logger = logging.getLogger(__name__)


# ─── Data Pipeline: Upload, Map, Import routes ────────────────────────


@codegen_bp.route("/solutions/<int:solution_id>/codegen/data/upload", methods=["POST"])
@login_required
def data_upload(solution_id):
    """Upload Excel/CSV file, return parsed sheets with column types."""
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    from app.modules.codegen.services.data_ingestion_service import DataIngestionService
    try:
        svc = DataIngestionService()
        sheets = svc.parse_file(file)
        # Strip all_rows from response (too large for JSON), keep sample_rows
        for sheet in sheets:
            sheet.pop("all_rows", None)
        return jsonify({"sheets": sheets})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@codegen_bp.route("/solutions/<int:solution_id>/codegen/data/auto-map", methods=["POST"])
@login_required
def data_auto_map(solution_id):
    """Auto-map uploaded columns to generated data model fields.

    Accepts optional column_types (from upload step) for type-based fallback matching.
    Uses LLM semantic matching when deterministic methods fail.
    """
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    body = request.get_json(silent=True) or {}
    sheet_columns = body.get("columns", [])
    column_types = body.get("column_types")  # Optional: {"col_name": "inferred_type"}
    if not sheet_columns:
        return jsonify({"error": "No columns provided"}), 400

    # Extract model fields from generated code
    gen = CodegenGeneration.query.filter_by(solution_id=solution_id).first()
    if not gen or not gen.generated_files:
        return jsonify({"error": "No generated code. Run code generation first."}), 400

    model_fields = _extract_model_fields(gen.generated_files)

    # Build field_types from generated code if column_types provided
    field_types = None
    if column_types:
        field_types = _extract_field_types(gen.generated_files)

    from app.modules.codegen.services.column_mapping_engine import ColumnMappingEngine
    engine = ColumnMappingEngine()
    # Pass extra kwargs only if the engine supports them
    try:
        mappings = engine.auto_map(
            sheet_columns,
            model_fields,
            column_types=column_types,
            field_types=field_types,
        )
    except TypeError:
        # Engine does not yet accept column_types/field_types — fall back
        mappings = engine.auto_map(sheet_columns, model_fields)
    return jsonify({"mappings": mappings})


@codegen_bp.route("/solutions/<int:solution_id>/codegen/data/add-field", methods=["POST"])
@login_required
def data_add_field(solution_id):
    """Add a new field to a deployed solution's data model.

    When execute=true in the request body, the migration is run on the deployed
    solution's database. Otherwise, returns the generated SQL and code patches
    for review before execution.
    """
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    body = request.get_json(silent=True) or {}
    entity = body.get("entity")
    field_name = body.get("field_name")
    field_type = body.get("field_type", "string")
    default_value = body.get("default_value")
    execute = body.get("execute", False)

    if not entity or not field_name:
        return jsonify({"error": "entity and field_name are required"}), 400

    from app.modules.codegen.services.live_migration_service import LiveMigrationService
    svc = LiveMigrationService()

    if execute:
        # Execute migration on deployed database
        from app.modules.codegen.models import SolutionInstance
        instance = SolutionInstance.query.filter_by(solution_id=solution_id).first()
        if not instance:
            return jsonify({"error": "Solution is not deployed. Deploy it first."}), 400

        result = svc.add_field(
            instance_id=instance.id,
            entity=entity,
            field_name=field_name,
            field_type=field_type,
            default=default_value,
        )

        if not result["success"]:
            return jsonify({
                "status": "error",
                "migration_sql": result.get("migration_sql", ""),
                "error": result["error"],
            }), 500

        return jsonify({
            "status": "executed",
            "migration_sql": result["migration_sql"],
            "model_patch": result.get("model_patch", ""),
            "schema_patch": result.get("schema_patch", ""),
            "message": f"Field '{field_name}' added to {entity} and migration executed.",
        })
    else:
        # Generate SQL and patches without executing
        sql = svc.generate_add_column_sql(
            table_name=entity.lower() + "s",
            field_name=field_name,
            field_type=field_type,
            default_value=default_value,
        )
        model_patch = svc.generate_model_code_patch(entity, field_name, field_type, default_value)
        schema_patch = svc.generate_schema_code_patch(field_name, field_type)

        return jsonify({
            "status": "ready",
            "migration_sql": sql,
            "model_patch": model_patch,
            "schema_patch": schema_patch,
            "message": f"Field '{field_name}' ready to add to {entity}. Set execute=true to run.",
        })


@codegen_bp.route("/solutions/<int:solution_id>/codegen/data/import", methods=["POST"])
@login_required
def data_import_execute(solution_id):
    """Execute data import: send mapped rows to the deployed solution's API.

    Request body:
        filename: str — original file name for tracking
        sheet_name: str — sheet name for tracking
        mappings: list — column mappings from auto-map step
        rows: list — row dicts from parsed file (all_rows)

    The endpoint:
    1. Validates inputs
    2. Creates a DataImport tracking record
    3. Transforms rows according to mappings
    4. Sends rows to the deployed solution's REST API (POST /api/<entity>)
    5. Updates the DataImport with results
    """
    solution = Solution.query.get_or_404(solution_id)
    _check_access(solution)

    body = request.get_json(silent=True) or {}
    mappings = body.get("mappings")
    rows = body.get("rows")
    filename = body.get("filename", "unknown")
    sheet_name = body.get("sheet_name", "Sheet1")

    if not mappings:
        return jsonify({"error": "mappings is required. Run auto-map first."}), 400
    if not rows:
        return jsonify({"error": "rows is required. Upload a file first."}), 400

    # Validate mappings are dicts with required keys
    if not isinstance(mappings, list) or not all(isinstance(m, dict) for m in mappings):
        return jsonify({"error": "mappings must be a list of mapping objects. Run auto-map first."}), 400

    # Filter to only mapped columns (confidence > 0)
    active_mappings = [m for m in mappings if m.get("target_field")]

    from app.modules.codegen.models import SolutionInstance

    # Check for deployment
    instance = SolutionInstance.query.filter_by(solution_id=solution_id).first()
    if not instance or not instance.deployment_url:
        return jsonify({
            "error": "Solution is not deployed. Deploy it first to import data.",
        }), 400

    # Create tracking record
    data_import = DataImport(
        solution_id=solution_id,
        filename=filename,
        sheet_name=sheet_name,
        row_count=len(rows),
        column_count=len(mappings),
        mapped_columns=len(active_mappings),
        unmapped_columns=len(mappings) - len(active_mappings),
        column_mappings=mappings,
        status="importing",
    )
    db.session.add(data_import)
    db.session.flush()

    # Transform and send rows
    try:
        result = _send_rows_to_instance(instance, active_mappings, rows)
        data_import.status = "completed"
        data_import.imported_count = result.get("imported", 0)
        data_import.error_count = len(result.get("errors", []))
        data_import.errors = result.get("errors") if result.get("errors") else None
        from datetime import datetime
        data_import.completed_at = datetime.utcnow()
    except Exception as e:
        data_import.status = "failed"
        data_import.errors = [{"row": 0, "error": str(e)}]
        data_import.error_count = 1
        logger.error("Data import failed for solution %s: %s", solution_id, e)

    db.session.commit()

    return jsonify({
        "import_id": data_import.id,
        "status": data_import.status,
        "imported_count": data_import.imported_count,
        "error_count": data_import.error_count,
        "errors": data_import.errors,
    })


def _send_rows_to_instance(instance, mappings, rows):
    """Transform rows according to mappings and POST to deployed solution's API.

    Groups rows by target entity, transforms column names, and sends
    batch POST requests to the deployed solution's REST endpoints.

    Args:
        instance: SolutionInstance with deployment_url
        mappings: List of active column mappings with target_entity/target_field
        rows: List of row dicts with source column keys

    Returns:
        {"imported": int, "errors": [{"row": int, "error": str}]}
    """
    import requests as http_requests

    # Group mappings by target entity
    entity_mappings: dict[str, list[dict]] = {}
    for m in mappings:
        entity = m["target_entity"]
        entity_mappings.setdefault(entity, []).append(m)

    total_imported = 0
    errors = []
    base_url = instance.deployment_url.rstrip("/")

    for entity, e_mappings in entity_mappings.items():
        # Build source->target column name map for this entity
        col_map = {m["source_column"]: m["target_field"] for m in e_mappings}

        # Transform and send each row
        endpoint = f"{base_url}/api/{entity.lower()}s"
        for row_idx, row in enumerate(rows):
            transformed = {}
            for src_col, tgt_field in col_map.items():
                if src_col in row:
                    transformed[tgt_field] = row[src_col]

            if not transformed:
                continue

            try:
                resp = http_requests.post(endpoint, json=transformed, timeout=10)
                if resp.status_code in (200, 201):
                    total_imported += 1
                else:
                    errors.append({
                        "row": row_idx + 1,
                        "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                    })
            except http_requests.RequestException as e:
                errors.append({"row": row_idx + 1, "error": str(e)})

    return {"imported": total_imported, "errors": errors}
