"""Explicit business-field contract for the two manual application writers.

The richer contract follows import_sophisticated_routes.get_import_fields's
categorized model fields, plus its supported last_backup_date. Virtual linking
fields are deliberately rejected: the manual handler has no safe link resolver.
No field is authorized merely because a model acquires a new column.
"""
import math

from app.services.application_status import status_for_write


def validate_manual_application_import(data, *, rich=False):
    options = {"applications", "duplicate_mode"}
    if rich:
        options.add("date_format")
    if not isinstance(data, dict) or set(data) - options:
        raise ValueError("Expected an object containing applications and import options")
    applications = data.get("applications")
    if not isinstance(applications, list) or not applications:
        raise ValueError("applications must be a non-empty list")
    mode = data.get("duplicate_mode", "merge")
    if not isinstance(mode, str) or mode not in ("merge", "update", "skip", "duplicate"):
        raise ValueError("duplicate_mode must be merge, update, skip or duplicate")
    mode = "merge" if mode == "update" else mode
    date_format = data.get("date_format", "iso")
    if not isinstance(date_format, str) or date_format not in ("iso", "dmy", "mdy"):
        raise ValueError("date_format must be iso, dmy or mdy")
    text_fields = {
        "name": 256, "app_id": 50, "application_code": 50,
        "component_type": 100, "deployment_status": 50, "description": None,
    }
    integers, numbers, booleans = set(), set(), set()
    if rich:
        text_fields.update({
            "version": 50, "application_type": 50, "application_category": 50,
            "deployment_model": 30, "criticality": 20, "business_criticality": 50,
            "lifecycle_status": 20, "strategic_importance": 20, "business_value": 20,
            "differentiation_level": 20, "business_domain": 100, "user_type": 100,
            "vendor_name": 100, "vendor_type": 30, "contract_type": 30, "support_level": 30,
            "primary_database": 200, "cache_technology": 200, "message_queue": 200,
            "api_documentation": 255, "integration_pattern": 100, "architecture_style": 30,
            "license_type": 100, "performance_rating": 20, "integration_complexity": 20,
            "data_architecture": 30, "data_classification": 255, "security_level": 255,
            "authorization_model": 50, "application_owner": 100, "business_owner": 100,
            "technical_owner": 100, "technical_lead": 100, "product_manager": 100,
            "development_team": 100, "support_team": 100, "architecture_domain": 100,
            "technical_risk": 20, "business_risk": 20, "vendor_risk": 20,
            "obsolescence_risk": 20, "scalability_model": 50, "backup_frequency": 50,
        })
        text_fields.update(dict.fromkeys((
            "imported_capabilities", "application_functions_text", "imported_apqc_codes",
            "business_purpose", "business_functions", "user_types", "technology_stack",
            "programming_languages", "frameworks", "database_platforms", "integration_methods",
            "compliance_requirements", "security_certifications", "authentication_method",
            "cloud_provider", "deployment_region", "container_image", "kubernetes_namespace",
            "notes", "assessment_notes",
            "implementation_date", "contract_expiry_date", "planned_retirement_date",
            "last_major_upgrade", "last_backup_date", "last_security_audit_date",
            "last_penetration_test_date", "go_live_date", "end_of_life_date",
        )))
        integers = set((
            "user_base_size", "user_count", "concurrent_users_max", "average_daily_users",
            "technology_age_years", "response_time_target_ms", "throughput_target_tps",
            "number_of_integrations", "interfaces_count", "dependencies_count", "rpo_hours", "rto_hours",
        ))
        numbers = set((
            "total_cost_of_ownership", "license_cost", "license_cost_annual", "maintenance_cost",
            "infrastructure_cost", "infrastructure_cost_monthly", "support_cost", "implementation_cost",
            "roi_score", "availability_target", "availability_actual", "user_satisfaction_score",
            "sla_availability_percentage", "current_uptime_percentage",
        ))
        booleans = set((
            "api_available", "exposes_api", "encryption_at_rest", "encryption_in_transit",
            "pii_data_processed", "gdpr_compliant", "disaster_recovery_enabled",
        ))
    allowed = set(text_fields) | integers | numbers | booleans
    cleaned = []
    for index, row in enumerate(applications, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Row {index}: expected an application object")
        unsupported = set(row) - allowed
        if unsupported:
            raise ValueError(f"Row {index}: unsupported fields: {', '.join(sorted(unsupported))}")
        values = {}
        for field, value in row.items():
            if value is None and field != "name":
                continue
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            if field in text_fields:
                if not isinstance(value, str):
                    raise ValueError(f"Row {index}: {field} must be text")
                limit = text_fields[field]
                if limit is not None and len(value) > limit:
                    raise ValueError(f"Row {index}: {field} exceeds {limit} characters")
                if field == "deployment_status":
                    value, refusal = status_for_write(value)
                    if refusal:
                        raise ValueError(f"Row {index}: {refusal}")
            elif field in booleans:
                if isinstance(value, str) and value.lower() in ("true", "false", "yes", "no", "1", "0"):
                    value = value.lower() in ("true", "yes", "1")
                if type(value) is not bool:
                    raise ValueError(f"Row {index}: {field} must be true or false")
            else:
                if type(value) not in (int, float, str):
                    raise ValueError(f"Row {index}: {field} must be numeric")
                try:
                    value = float(value)
                except (ValueError, OverflowError):
                    raise ValueError(f"Row {index}: {field} must be numeric") from None
                if not math.isfinite(value):
                    raise ValueError(f"Row {index}: {field} must be finite")
                if field in integers:
                    if not value.is_integer() or not -(2**31) <= value < 2**31:
                        raise ValueError(f"Row {index}: {field} must be a 32-bit integer")
                    value = int(value)
            values[field] = value
        if not values.get("name"):
            raise ValueError(f"Row {index}: Name is required")
        code_alias = values.pop("app_id", None)
        if code_alias:
            if values.get("application_code", code_alias) != code_alias:
                raise ValueError(f"Row {index}: app_id and application_code disagree")
            values["application_code"] = code_alias
        cleaned.append(values)
    return cleaned, mode, date_format
