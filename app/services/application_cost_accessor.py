"""
Application Cost Accessor — the single accessor for "annual cost of an application".
The system of record for annual cost is ApplicationComponent.total_cost_of_ownership.
This module provides the one read/write path that all import screens must use.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from flask import current_app

from app.models.application_portfolio import ApplicationComponent


# The canonical annual-cost column on ApplicationComponent.
# Model comment: "Annual TCO".
_ANNUAL_COST_COLUMN = "total_cost_of_ownership"

# Recognised cost categories for typed import mapping.
COST_CATEGORIES = frozenset({
    "total_cost_of_ownership",
    "license_cost_annual",
    "maintenance_cost",
    "infrastructure_cost",
    "infrastructure_cost_monthly",
    "support_cost",
    "implementation_cost",
    "development_cost_annual",
})

# Recognised period values for normalisation.
PERIOD_VALUES = frozenset({"annual", "monthly"})


def get_reporting_currency() -> str:
    """Return the default reporting currency code from app config."""
    return current_app.config.get("DEFAULT_CURRENCY", "GBP")


def get_annual_cost(app: ApplicationComponent) -> Optional[Decimal]:
    """
    Return the application's annual cost as Decimal, or None if not recorded.

    All screens, reports and exports that need "the annual cost of this
    application" must call this.
    """
    value = getattr(app, _ANNUAL_COST_COLUMN, None)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def get_annual_cost_float(app: ApplicationComponent) -> Optional[float]:
    """Convenience wrapper returning float for templates that expect it."""
    d = get_annual_cost(app)
    return float(d) if d is not None else None


# R1-B08 PR 2: the legacy per-category columns (license_cost, maintenance_cost,
# infrastructure_cost) predate this accessor and are not backfilled into
# total_cost_of_ownership for rows imported before PR 1 landed -- only rows
# imported through set_annual_cost since then are. A caller that switched to
# get_annual_cost() alone would read every pre-PR-1 app as having no cost data
# at all, which is worse than the scattered-columns status quo it replaces.
# These two helpers are the one home for "does this app have recorded cost at
# all" and "what number, from where" during that transition; Release 2's Cost
# Fact consolidation retires them once every row is backfilled.
_LEGACY_COST_FIELDS = ("license_cost", "maintenance_cost", "infrastructure_cost")


def has_recorded_cost(app: ApplicationComponent) -> bool:
    """True if the canonical column or any pre-consolidation legacy column
    carries a positive cost value."""
    canonical = get_annual_cost(app)
    if canonical is not None and canonical > 0:
        return True
    for field in _LEGACY_COST_FIELDS:
        value = getattr(app, field, None)
        if value is not None and float(value) > 0:
            return True
    return False


def get_annual_cost_with_source(app: ApplicationComponent):
    """(value, source_label). Prefers the canonical column; falls back to
    the sum of the legacy per-category columns for a row never re-imported
    through set_annual_cost, labelled so the caller can show its provenance."""
    canonical = get_annual_cost(app)
    if canonical is not None and canonical > 0:
        return float(canonical), "ApplicationComponent.total_cost_of_ownership"
    legacy_values = [getattr(app, field, None) for field in _LEGACY_COST_FIELDS]
    if any(v is not None for v in legacy_values):
        total = sum(float(v or 0) for v in legacy_values)
        return total, "ApplicationComponent (" + " + ".join(_LEGACY_COST_FIELDS) + ")"
    return None, None


def set_annual_cost(app: ApplicationComponent, value: Optional[Decimal]) -> None:
    """
    Write the application's annual cost through the accessor.

    Accepts Decimal, int, float, or numeric string. None clears the field.
    Negative values are rejected (treated as not recorded).
    """
    if value is None:
        setattr(app, _ANNUAL_COST_COLUMN, None)
        return
    try:
        parsed = Decimal(str(value))
        if parsed < 0:
            # Negative cost values are not accepted — treated as not recorded.
            setattr(app, _ANNUAL_COST_COLUMN, None)
            return
        setattr(app, _ANNUAL_COST_COLUMN, parsed)
    except (InvalidOperation, ValueError, TypeError):
        # Invalid input is treated as "not recorded" — never stored as 0.
        setattr(app, _ANNUAL_COST_COLUMN, None)


def parse_cost_cell(
    raw_value: Any,
    currency: Optional[str] = None,
    period: str = "annual",
    category: str = "total_cost_of_ownership",
) -> Dict[str, Any]:
    """
    Parse a single cost cell from an import spreadsheet.

    Returns a dict with keys:
        - "value": Decimal or None (None means unparseable → import as empty)
        - "currency": normalised currency code (e.g. "USD"), or None if not provided
        - "period": normalised period ("annual" or "monthly")
        - "category": normalised category (one of COST_CATEGORIES)
        - "warnings": list of warning strings (empty if clean)
        - "error": error string if unparseable, else None

    An unparseable cell is reported via "error" and "value" = None.
    It is NEVER stored as 0.
    """
    warnings: List[str] = []
    errors: List[str] = []
    value: Optional[Decimal] = None

    # Normalise currency (None means "not specified" — caller should
    # resolve against the organisation's reporting currency)
    if currency is not None:
        currency = currency.strip().upper()
        if len(currency) != 3:
            errors.append(f"Currency '{currency}' is not a 3-letter code")
            currency = None

    # Normalise period
    period = (period or "annual").strip().lower()
    if period not in PERIOD_VALUES:
        errors.append(f"Unknown period '{period}'; valid values: annual, monthly")
        # Value is rejected when period is unrecognised
        value = None

    # Normalise category
    category = (category or "total_cost_of_ownership").strip().lower()
    if category not in COST_CATEGORIES:
        warnings.append(f"Unknown cost category '{category}'; defaulting to total_cost_of_ownership")
        category = "total_cost_of_ownership"

    # Parse the numeric value — skip if an earlier check (period) already set error
    if errors:
        pass
    elif raw_value is None or (isinstance(raw_value, str) and raw_value.strip() == ""):
        # Blank cell means "no value" — not an error
        pass
    else:
        try:
            # Strip common currency symbols and whitespace
            cleaned = str(raw_value).strip()
            for ch in ["$", "€", "£", "¥", " "]:
                cleaned = cleaned.replace(ch, "")
            # Handle parentheses as negative (accounting format)
            if cleaned.startswith("(") and cleaned.endswith(")"):
                cleaned = "-" + cleaned[1:-1]
            
            # Handle European vs US/UK number formats
            # European: 1.234,56 (dot=thousands, comma=decimal)
            # US/UK: 1,234.56 (comma=thousands, dot=decimal)
            # Heuristic: if both comma and dot present, the last one is decimal separator
            # If only one type present, assume dot=decimal (US/UK) unless comma count suggests European
            has_comma = ',' in cleaned
            has_dot = '.' in cleaned
            
            if has_comma and has_dot:
                # Both present - last one wins as decimal separator
                last_comma = cleaned.rfind(',')
                last_dot = cleaned.rfind('.')
                if last_comma > last_dot:
                    # European format: comma is decimal separator
                    cleaned = cleaned.replace('.', '')  # Remove thousands separators
                    cleaned = cleaned.replace(',', '.')  # Convert decimal separator
                else:
                    # US/UK format: dot is decimal separator
                    cleaned = cleaned.replace(',', '')  # Remove thousands separators
            elif has_comma:
                # Only commas - could be European decimal or US thousands
                # Heuristic: if comma is followed by exactly 2 digits at end, treat as decimal
                parts = cleaned.split(',')
                if len(parts) == 2 and len(parts[1]) == 2 and parts[1].isdigit():
                    # Likely European decimal format (e.g., "99,50" or "1234,56")
                    cleaned = cleaned.replace(',', '.')
                else:
                    # Likely US thousands separators (e.g., "1,234" or "1,234,567")
                    cleaned = cleaned.replace(',', '')
            # If only dots, assume US/UK decimal format (e.g., "1234.56")
            # No change needed
            
            value = Decimal(cleaned)
            if value < 0:
                errors.append(f"Negative cost value not accepted: {raw_value!r}")
                value = None
        except (InvalidOperation, ValueError, TypeError):
            errors.append(f"Could not parse cost value: {raw_value!r}")
            value = None

    # Normalise monthly to annual for storage
    annual_value: Optional[Decimal] = None
    if value is not None and period == "monthly":
        annual_value = value * 12
    else:
        annual_value = value

    return {
        "value": annual_value,
        "currency": currency,
        "period": period,
        "category": category,
        "warnings": warnings,
        "error": "; ".join(errors) if errors else None,
    }


def map_import_cost_columns(
    row: Dict[str, Any],
    column_mapping: Dict[str, str],
    reporting_currency: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract and parse cost columns from an import row using a column mapping.

    Args:
        row: The raw row dict from the parsed file.
        column_mapping: Dict mapping logical cost fields to column names in the file.
            Supported keys: "total_cost_of_ownership", "license_cost_annual",
            "maintenance_cost", "infrastructure_cost", "support_cost",
            "implementation_cost", "development_cost_annual",
            plus optional "currency", "period", "category" for per-row overrides.
        reporting_currency: The organisation's reporting currency (3-letter code).
            When set, a cell whose parsed currency does not match is rejected
            and reported as an error rather than stored.

    Returns:
        Dict with parsed cost fields ready for the accessor, plus a "cost_warnings"
        list and a "cost_errors" dict mapping field names to error strings.
    """
    result: Dict[str, Any] = {}
    cost_warnings: List[str] = []
    cost_errors: Dict[str, str] = {}

    # Global overrides (apply to all cost fields in this row if present)
    has_currency_col = column_mapping.get("currency")
    global_currency = row.get(has_currency_col, None) if has_currency_col else None
    global_period = row.get(column_mapping.get("period", ""), "annual") if column_mapping.get("period") else "annual"

    # Determine effective reporting currency
    effective_reporting = reporting_currency

    for field_name in COST_CATEGORIES:
        col_name = column_mapping.get(field_name)
        if not col_name or col_name not in row:
            continue

        raw_value = row[col_name]
        currency = row.get(has_currency_col, global_currency) if has_currency_col else global_currency
        period = row.get(column_mapping.get("period", ""), global_period) if column_mapping.get("period") else global_period

        parsed = parse_cost_cell(raw_value, currency, period, field_name)

        if parsed["error"]:
            cost_errors[field_name] = parsed["error"]
            # Value stays None → will be imported as empty
        else:
            # Reject cells whose currency explicitly differs from reporting currency
            cell_currency = parsed["currency"]
            if cell_currency is not None and effective_reporting and cell_currency != effective_reporting:
                cost_errors[field_name] = (
                    f"Currency '{cell_currency}' does not match "
                    f"reporting currency '{effective_reporting}'"
                )
            else:
                if parsed["value"] is not None:
                    result[field_name] = parsed["value"]

        cost_warnings.extend(parsed["warnings"])

    return {
        "cost_fields": result,
        "cost_warnings": cost_warnings,
        "cost_errors": cost_errors,
    }


def apply_cost_to_application(
    app: ApplicationComponent,
    cost_fields: Dict[str, Optional[Decimal]],
) -> None:
    """
    Apply parsed cost fields to an ApplicationComponent via the accessor.

    Writes total_cost_of_ownership (the system-of-record annual-cost column)
    and each recognised cost category to its corresponding model column.
    A field is written only when its key is present in cost_fields, so
    a missing or unparseable cell never clears a stored value.

    The non-TCO categories (license, maintenance, infrastructure, support,
    implementation, development) are written to their model columns for
    future releases; they may not yet be rendered in all portfolio views.
    """
    if "total_cost_of_ownership" in cost_fields:
        set_annual_cost(app, cost_fields["total_cost_of_ownership"])

    # Write other cost categories to their model columns when present
    other_fields = [k for k in COST_CATEGORIES if k != "total_cost_of_ownership"]
    for field_name in other_fields:
        if field_name in cost_fields:
            try:
                val = cost_fields[field_name]
                if val is None:
                    setattr(app, field_name, None)
                else:
                    setattr(app, field_name, float(Decimal(str(val))))
            except (InvalidOperation, ValueError, TypeError):
                setattr(app, field_name, None)


# Cost column variant definitions for case-insensitive header detection.
# Each logical field maps to a list of recognised column-name spellings.
_COST_COLUMN_VARIANTS: Dict[str, List[str]] = {
    "total_cost_of_ownership": [
        "total_cost_of_ownership", "tco", "annual_cost", "annual_tco",
        "total cost of ownership", "Total Cost of Ownership", "TCO",
        "Annual Cost", "Annual TCO",
    ],
    "license_cost_annual": [
        "license_cost_annual", "license_cost", "licence_cost", "annual_license_cost",
        "license cost", "License Cost", "Annual License Cost",
    ],
    "maintenance_cost": [
        "maintenance_cost", "annual_maintenance_cost", "maintenance cost",
        "Maintenance Cost", "Annual Maintenance Cost",
    ],
    "infrastructure_cost": [
        "infrastructure_cost", "annual_infrastructure_cost", "infra_cost",
        "infrastructure cost", "Infrastructure Cost", "Annual Infrastructure Cost",
    ],
    "infrastructure_cost_monthly": [
        "infrastructure_cost_monthly", "monthly_infrastructure_cost", "infra_cost_monthly",
        "infrastructure cost monthly", "Infrastructure Cost Monthly", "Monthly Infrastructure Cost",
    ],
    "support_cost": [
        "support_cost", "annual_support_cost", "support cost",
        "Support Cost", "Annual Support Cost",
    ],
    "implementation_cost": [
        "implementation_cost", "implementation cost", "Implementation Cost",
    ],
    "development_cost_annual": [
        "development_cost_annual", "dev_cost", "annual_development_cost",
        "development cost", "Development Cost", "Annual Development Cost",
    ],
    "currency": [
        "currency", "cost_currency", "Currency", "Cost Currency",
    ],
    "period": [
        "period", "cost_period", "billing_period", "Period", "Cost Period",
    ],
    "category": [
        "cost_category", "cost_type", "Cost Category", "Cost Type",
    ],
}


def detect_cost_columns(
    columns: List[str],
) -> Dict[str, str]:
    """
    Detect cost-column mapping from a list of available column headers.

    Matching is case-insensitive and ignores surrounding whitespace.
    Returns a dict mapping logical cost field names to the matched column name.
    """
    mapping: Dict[str, str] = {}
    columns_lower = {c.strip().lower(): c for c in columns}
    for field_name, variants in _COST_COLUMN_VARIANTS.items():
        for variant in variants:
            key = variant.strip().lower()
            if key in columns_lower:
                mapping[field_name] = columns_lower[key]
                break
    return mapping


def detect_cost_columns_from_dict(
    source: Dict[str, Any],
) -> Dict[str, str]:
    """
    Detect cost-column mapping from a dictionary's keys.

    Convenience wrapper around detect_cost_columns for dict-based source data.
    """
    return detect_cost_columns(list(source.keys()))


def get_cost_summary_for_org(org_id: int) -> Dict[str, Any]:
    """
    Aggregate cost summary for an organisation (used by portfolio totals).

    Returns dict with total_annual_cost (None when nothing recorded),
    application_count, applications_with_cost.
    """
    from app import db
    from sqlalchemy import text

    result = db.session.execute(
        text("""
            SELECT 
                COUNT(*) as application_count,
                COUNT(total_cost_of_ownership) as applications_with_cost,
                SUM(total_cost_of_ownership) as total_annual_cost
            FROM application_components
            WHERE organization_id = :org_id
        """),
        {"org_id": org_id}
    ).fetchone()

    apps_with_cost = result.applications_with_cost or 0

    return {
        "total_annual_cost": Decimal(str(result.total_annual_cost)) if result.total_annual_cost is not None and apps_with_cost > 0 else None,
        "application_count": result.application_count or 0,
        "applications_with_cost": apps_with_cost,
    }