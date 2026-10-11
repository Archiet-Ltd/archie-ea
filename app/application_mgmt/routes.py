"""
Application Management Routes

Dashboard and CRUD operations for Application Layer elements.
"""
# mass-deletion-ok — BE-179 removes 16 manual CSRF blocks replaced by global CSRFProtect

# Side-effect import, NOT dead code: this is the only module that imports
# app/models/metrics.py, and that import is what registers the
# `application_metrics_snapshots` table on db.metadata. Removing it (as
# `ruff --fix --select F401` did) silently drops the table from the ORM —
# caught by comparing db.metadata.tables before and after.
from ..models.metrics import ApplicationMetricsSnapshot  # noqa: F401
import json
import logging
import re
from datetime import datetime  # dead-code-ok

# Define the logger
logger = logging.getLogger(__name__)

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import or_, select, text  # dead-code-ok: func
from sqlalchemy.orm import joinedload

from config import CurrencyConfig
from .. import db  # dead-code-ok
from ..models.application_layer import (
    ApplicationInterface,
    ApplicationService,
    DataObject,
)
from ..models.application_portfolio import ApplicationComponent

# Import the missing model
from ..models.models import ArchiMateElement, ArchiMateRelationship  # dead-code-ok: ArchitectureModel
from ..models.motivation import Driver, Goal
from . import application_mgmt

# =============================================================================
# HELPER FUNCTIONS FOR DATA IMPORT
# =============================================================================


def parse_integer_from_range(value):
    """
    Parse an integer value that may be a range string like '101 - 500'.
    Returns the midpoint for ranges, or extracts the first number found.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    str_val = str(value).strip()
    if not str_val:
        return None

    # Try direct integer conversion first
    try:
        return int(str_val)
    except ValueError:
        logger.exception("Failed to parse integer value")

    # Try to extract from range format like "101 - 500" or "101 - 500"
    range_match = re.match(r"(\d+)\s*[-–]\s*(\d+)", str_val)
    if range_match:
        lower = int(range_match.group(1))
        upper = int(range_match.group(2))
        # Return the midpoint of the range
        return (lower + upper) // 2

    # Try to extract from formats like "< 50", "> 1000", "50+", etc.
    less_than_match = re.match(r"[<≤]\s*(\d+)", str_val)
    if less_than_match:
        return int(less_than_match.group(1))

    greater_than_match = re.match(r"[>≥]\s*(\d+)", str_val)
    if greater_than_match:
        return int(greater_than_match.group(1))

    plus_match = re.match(r"(\d+)\s*\+", str_val)
    if plus_match:
        return int(plus_match.group(1))

    # Try to extract any number from the string
    num_match = re.search(r"(\d+)", str_val)
    if num_match:
        return int(num_match.group(1))

    return None


# Integer fields that need special range parsing during import
INTEGER_RANGE_FIELDS = {
    "user_base_size",
    "user_count",
    "concurrent_users_max",
    "average_daily_users",
    "interfaces_count",
    "number_of_integrations",
    "dependencies_count",
    "rpo_hours",
    "rto_hours",
    "technology_age_years",
    "max_instances",
    "min_instances",
    "response_time_target_ms",
    "throughput_target_tps",
    "current_response_time_ms",
    "current_throughput_tps",
}


# =============================================================================
# FLEXIBLE COLUMN MAPPING FOR CSV/JSON IMPORTS
# =============================================================================
# Supports various column name formats to handle different CSV templates

IMPORT_COLUMN_ALIASES = {
    "name": [
        "Name",
        "Application Name",
        "App Name",
        "Application",
        "AppName",
        "app_name",
    ],
    "component_type": [
        "Type",
        "Component Type",
        "App Type",
        "Application Type",
        "ComponentType",
        "component_type",
    ],
    "application_category": [
        "Category",
        "Application Category",
        "App Category",
        "ApplicationCategory",
        "application_category",
    ],
    "technology_stack": [
        "Technology Stack",
        "Tech Stack",
        "Technologies",
        "Stack",
        "technology_stack",
        "TechnologyStack",
    ],
    "version": ["Version", "App Version", "version", "Ver"],
    "deployment_status": [
        "Status",
        "Deployment Status",
        "Deploy Status",
        "deployment_status",
        "DeploymentStatus",
    ],
    "business_domain": [
        "Business Domain",
        "Domain",
        "Business Area",
        "business_domain",
        "BusinessDomain",
    ],
    "business_owner": [
        "Owner",
        "Business Owner",
        "App Owner",
        "business_owner",
        "BusinessOwner",
    ],
    "development_team": [
        "Team",
        "Development Team",
        "Dev Team",
        "development_team",
        "DevelopmentTeam",
    ],
    "user_base_size": [
        "Users",
        "User Count",
        "User Base Size",
        "Number of Users",
        "user_count",
        "UserCount",
        "user_base_size",
    ],
    "business_criticality": [
        "Criticality",
        "Business Criticality",
        "business_criticality",
        "BusinessCriticality",
        "Critical",
    ],
    "capabilities": [
        "Capabilities",
        "Capability",
        "Business Capabilities",
        "Supported Capabilities",
        "capabilities",
    ],
    "business_process": [
        "Business Process",
        "Process",
        "PCF Process",
        "Processes",
        "PCF",
        "business_process",
        "BusinessProcess",
    ],
    "description": ["Description", "App Description", "description", "Desc"],
    "notes": ["Notes", "Comments", "Remarks", "notes"],
}

# Maximum field lengths to prevent truncation errors
# Note: Values match database schema after migrations
FIELD_MAX_LENGTHS = {
    "application_category": 100,  # Expanded to VARCHAR(100) directly
    "component_type": 100,
    "business_domain": 100,
    "business_criticality": 100,
    "deployment_status": 100,
    "version": 50,
    "business_owner": 200,
    "development_team": 200,
    "technology_stack": 500,
    "access_mode": 100,
    "user_type": 100,
    "lifecycle_status": 100,
    "support_level": 100,
    "managed_type": 100,
}


def _get_column_value(row, target_field, clean_func=None):
    """
    Get value from row using flexible column name matching.

    Tries all known aliases for a target field to handle various CSV formats.

    Args:
        row: Dict containing CSV row data
        target_field: The canonical field name to look up
        clean_func: Optional function to clean/sanitize the value

    Returns:
        The cleaned value or None if not found
    """
    aliases = IMPORT_COLUMN_ALIASES.get(target_field, [target_field])

    for alias in aliases:
        if alias in row:
            value = row[alias]
            if value is not None and str(value).strip():
                if clean_func:
                    return clean_func(value)
                return value
    return None


def _truncate_to_length(value, field_name, max_length=None):
    """
    Safely truncate a string value to prevent database truncation errors.

    Args:
        value: The string value to truncate
        field_name: The field name (for looking up max length)
        max_length: Optional override for maximum length

    Returns:
        Truncated string or original value
    """
    if value is None:
        return None

    value = str(value).strip()
    if not value:
        return None

    limit = max_length or FIELD_MAX_LENGTHS.get(field_name, 255)

    if len(value) > limit:
        # Truncate and add ellipsis if significantly over
        return value[: limit - 3] + "..." if limit > 10 else value[:limit]

    return value


def _validate_and_clean_import_row(row, clean_func):
    """
    Validate and clean an import row, applying truncation where needed.

    Args:
        row: Dict containing CSV row data
        clean_func: Function to clean/strip values

    Returns:
        Dict with cleaned and validated values
    """
    cleaned = {}

    # Get each field using flexible column matching
    cleaned["name"] = _get_column_value(row, "name", clean_func)
    cleaned["component_type"] = _truncate_to_length(
        _get_column_value(row, "component_type", clean_func), "component_type"
    )
    cleaned["application_category"] = _truncate_to_length(
        _get_column_value(row, "application_category", clean_func),
        "application_category",
    )
    cleaned["technology_stack"] = _truncate_to_length(
        _get_column_value(row, "technology_stack", clean_func), "technology_stack"
    )
    cleaned["version"] = _truncate_to_length(
        _get_column_value(row, "version", clean_func), "version"
    )
    cleaned["deployment_status"] = (
        _truncate_to_length(
            _get_column_value(row, "deployment_status", clean_func), "deployment_status"
        )
        or "planned"
    )
    cleaned["business_domain"] = _truncate_to_length(
        _get_column_value(row, "business_domain", clean_func), "business_domain"
    )
    cleaned["business_owner"] = _truncate_to_length(
        _get_column_value(row, "business_owner", clean_func), "business_owner"
    )
    cleaned["development_team"] = _truncate_to_length(
        _get_column_value(row, "development_team", clean_func), "development_team"
    )
    cleaned["business_criticality"] = _truncate_to_length(
        _get_column_value(row, "business_criticality", clean_func),
        "business_criticality",
    )

    # These don't need truncation - they go to linking functions
    cleaned["capabilities"] = _get_column_value(row, "capabilities", clean_func)
    cleaned["business_process"] = _get_column_value(row, "business_process", clean_func)
    cleaned["description"] = _get_column_value(row, "description", clean_func)
    cleaned["notes"] = _get_column_value(row, "notes", clean_func)

    # User count needs special parsing (handled separately)
    cleaned["user_count_raw"] = _get_column_value(row, "user_count")

    return cleaned


def _find_process_by_name_enhanced(name):
    """
    Find BusinessProcess with multi-stage matching.

    Stages:
    1. Exact case-insensitive match
    2. Process code match (for "5.4.2" format)
    3. Partial name match (contains)
    4. Fuzzy match using difflib

    Args:
        name: Process name or code to search for

    Returns:
        BusinessProcess instance or None
    """
    import difflib


    if not name:
        return None

    name = name.strip()

    # Stage 1: Exact case-insensitive match on name
    process = BusinessProcess.query.filter(BusinessProcess.name.ilike(name)).first()
    if process:
        return process

    # Stage 2: Process code match (for formats like "5.4.2" or "5.4.2 Description")
    # Extract numeric code if present
    code_match = re.match(r"^(\d+(?:\.\d+)*)", name)
    if code_match:
        code = code_match.group(1)
        process = BusinessProcess.query.filter(
            or_(
                BusinessProcess.process_code.ilike(code),
                BusinessProcess.process_code.ilike(f"{code}%"),
                BusinessProcess.process_code.ilike(f"%{code}"),
            )
        ).first()
        if process:
            return process

    # Stage 3: Partial name match (name contains search term)
    process = BusinessProcess.query.filter(
        or_(
            BusinessProcess.name.ilike(f"%{name}%"),
            BusinessProcess.process_code.ilike(f"%{name}%"),
        )
    ).first()
    if process:
        return process

    # Stage 4: Fuzzy match using difflib (lowered cutoff from 0.8 to 0.6)
    all_procs = BusinessProcess.query.with_entities(
        BusinessProcess.id, BusinessProcess.name
    ).all()

    if all_procs:
        proc_dict = {p.name: p.id for p in all_procs if p.name}
        matches = difflib.get_close_matches(name, proc_dict.keys(), n=1, cutoff=0.6)
        if matches:
            return BusinessProcess.query.get(proc_dict[matches[0]])

    return None


def _link_application_to_processes(app, functional_capabilities_str):
    """
    Link an application to PCF processes/functional capabilities.

    Uses multi-stage matching to find processes:
    1. Exact name match
    2. Process code match (e.g., "5.4.2")
    3. Partial name match
    4. Fuzzy match

    Args:
        app: ApplicationComponent instance
        functional_capabilities_str: String of comma/semicolon separated process names or codes

    Returns:
        dict: {'linked': int, 'not_found': list, 'matched': list}
    """

    if not functional_capabilities_str or not app:
        return {"linked": 0, "not_found": [], "matched": []}

    # Parse the capabilities string (supports comma, semicolon, pipe separators)
    capabilities = [
        c.strip() for c in re.split(r"[,;|]", functional_capabilities_str) if c.strip()
    ]

    # Prefetch existing mappings to avoid N+1 query
    existing_mappings_query = ApplicationProcessSupport.query.filter_by(
        application_component_id=app.id
    ).all()
    existing_mapping_keys = {
        mapping.business_process_id for mapping in existing_mappings_query
    }

    linked_count = 0
    not_found = []
    matched = []

    for cap_name in capabilities:
        # Use enhanced multi-stage matching
        process = _find_process_by_name_enhanced(cap_name)

        if process:
            # Check if mapping already exists using prefetched set
            if process.id not in existing_mapping_keys:
                # Create new mapping
                mapping = ApplicationProcessSupport(
                    application_component_id=app.id,
                    business_process_id=process.id,
                    support_type="primary_execution",  # Default support type
                    automation_level=50,  # Default 50% automation
                    criticality="medium",
                    is_active=True,
                    created_date=datetime.utcnow(),
                    notes=f"Auto-linked from import: {cap_name}",
                )
                db.session.add(mapping)
                existing_mapping_keys.add(
                    process.id
                )  # Update set to prevent duplicates
                linked_count += 1
                matched.append({"input": cap_name, "matched_to": process.name})
            else:
                matched.append(
                    {"input": cap_name, "matched_to": process.name, "existing": True}
                )
        else:
            not_found.append(cap_name)

    # Also store in business_functions JSON field for reference
    if app and capabilities:
        existing_functions = []
        if app.business_functions:
            try:
                existing_functions = json.loads(app.business_functions)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse business_functions JSON: {e}")
                existing_functions = []

        # Add new capabilities without duplicates
        for cap in capabilities:
            if cap not in existing_functions:
                existing_functions.append(cap)

        app.business_functions = json.dumps(existing_functions)

    return {"linked": linked_count, "not_found": not_found, "matched": matched}


def _link_application_to_apqc_by_ids(app, apqc_matches):
    """
    Link an application to APQC processes using pre-computed semantic match IDs.

    This function is used after semantic APQC classification has identified
    matching APQC processes. It finds or creates corresponding BusinessProcess
    records and then creates ApplicationProcessSupport records.

    Data model:
    - APQCProcess: Standard APQC framework processes (e.g., "3.1 Manage sales")
    - BusinessProcess: Actual business processes, linked via apqc_process_id
    - ApplicationProcessSupport: Links Applications to BusinessProcesses

    Args:
        app: ApplicationComponent instance
        apqc_matches: List of dicts with 'existing_id' (APQCProcess.id), 'process_code',
                     'process_name', 'similarity_score', 'source', etc.

    Returns:
        dict: {'linked': int, 'skipped': int, 'created_processes': int, 'errors': list}
    """

    from app.models.apqc_process import APQCProcess

    from ..models.relationship_tables import ApplicationProcessSupport

    if not apqc_matches or not app:
        return {"linked": 0, "skipped": 0, "created_processes": 0, "errors": []}

    # Prefetch all necessary data to avoid N+1 queries
    apqc_ids = [m.get("existing_id") for m in apqc_matches if m.get("existing_id")]
    process_codes = [
        m.get("process_code") for m in apqc_matches if m.get("process_code")
    ]

    # Prefetch APQC processes
    apqc_by_id = (
        {p.id: p for p in APQCProcess.query.filter(APQCProcess.id.in_(apqc_ids)).all()}
        if apqc_ids
        else {}
    )
    apqc_by_code = (
        {
            p.process_code: p
            for p in APQCProcess.query.filter(
                APQCProcess.process_code.in_(process_codes)
            ).all()
        }
        if process_codes
        else {}
    )

    # Prefetch BusinessProcesses linked to APQC processes
    all_apqc_ids = list(apqc_by_id.keys()) + [p.id for p in apqc_by_code.values()]
    business_by_apqc = (
        {
            bp.apqc_process_id: bp
            for bp in BusinessProcess.query.filter(
                BusinessProcess.apqc_process_id.in_(all_apqc_ids)
            ).all()
        }
        if all_apqc_ids
        else {}
    )

    # Prefetch existing mappings
    existing_mappings = ApplicationProcessSupport.query.filter_by(
        application_component_id=app.id
    ).all()
    existing_mapping_keys = {
        mapping.business_process_id for mapping in existing_mappings
    }

    linked_count = 0
    skipped_count = 0
    created_processes = 0
    errors = []

    for match in apqc_matches:
        try:
            # Get the APQC process ID from the semantic match
            apqc_id = match.get("existing_id")
            process_code = match.get("process_code", "")
            match.get("process_name", "")
            similarity_score = match.get("similarity_score", 0)
            source = match.get("source", "semantic_similarity")

            # Find the APQC process using prefetched data
            apqc_process = None
            if apqc_id:
                apqc_process = apqc_by_id.get(apqc_id)
            if not apqc_process and process_code:
                apqc_process = apqc_by_code.get(process_code)

            if not apqc_process:
                errors.append(f"APQC process not found: {process_code or apqc_id}")
                continue

            # Find or create a BusinessProcess linked to this APQC process using prefetched data
            business_process = business_by_apqc.get(apqc_process.id)

            if not business_process:
                # Create a new BusinessProcess based on the APQC process
                business_process = BusinessProcess(
                    name=apqc_process.process_name,  # Use clean database name only
                    process_code=f"AUTO-{apqc_process.process_code}",
                    description=apqc_process.process_description
                    or f"Auto-created from APQC: {apqc_process.process_code}",
                    apqc_process_id=apqc_process.id,
                    process_type="core",
                    status="active",  # Use correct field name
                )
                db.session.add(business_process)
                db.session.flush()  # Get the ID
                business_by_apqc[apqc_process.id] = business_process  # Update cache
                created_processes += 1

            # Check if mapping already exists using prefetched set
            if business_process.id in existing_mapping_keys:
                skipped_count += 1
                continue

            # Determine confidence level based on similarity score
            confidence = (
                "high"
                if float(similarity_score) >= 0.7
                else "medium"
                if float(similarity_score) >= 0.5
                else "low"
            )

            # Create new mapping
            mapping = ApplicationProcessSupport(
                application_component_id=app.id,
                business_process_id=business_process.id,
                support_type="primary_execution",
                automation_level=50,
                criticality="medium",
                is_active=True,
                notes=f"AI auto-linked ({source}): {process_code} [confidence: {confidence}, score: {float(similarity_score):.3f}]",
            )
            db.session.add(mapping)
            existing_mapping_keys.add(
                business_process.id
            )  # Update cache to prevent duplicates
            linked_count += 1

        except Exception as e:
            errors.append(
                f"Error linking {match.get('process_code', 'unknown')}: {str(e)}"
            )
            continue

    return {
        "linked": linked_count,
        "skipped": skipped_count,
        "created_processes": created_processes,
        "errors": errors,
    }


def _organize_apqc_links_by_row(apqc_link_list):
    """
    Organize a flat list of APQC links into a dictionary keyed by row index.

    This transforms the preview endpoint's apqc_details["link"] list into a format
    suitable for the execution endpoint's apqc_links_by_row parameter.

    Args:
        apqc_link_list: List of dicts with 'row', 'existing_id', 'process_code', etc.

    Returns:
        dict: {"row_index": [list of link dicts], ...}
    """
    by_row = {}
    for link in apqc_link_list:
        row = link.get("row")
        if row is not None:
            row_key = str(row)
            if row_key not in by_row:
                by_row[row_key] = []
            by_row[row_key].append(link)
    return by_row


def _find_capability_by_name_enhanced(name):
    """
    Find UnifiedCapability with multi-stage matching.

    Stages:
    1. Exact case-insensitive match
    2. Capability code match (if code field exists)
    3. Partial name match (contains)
    4. Fuzzy match using difflib

    Args:
        name: Capability name or code to search for

    Returns:
        UnifiedCapability instance or None
    """
    import difflib

    from ..models.unified_capability import UnifiedCapability

    if not name:
        return None

    name = name.strip()

    # Stage 1: Exact case-insensitive match on name
    capability = UnifiedCapability.query.filter(
        UnifiedCapability.name.ilike(name)
    ).first()
    if capability:
        return capability

    # Stage 2: Try matching by code if it looks like a code
    if hasattr(UnifiedCapability, "code"):
        code_match = re.match(r"^([A-Z]{2,4}[-_]?\d*)", name.upper())
        if code_match:
            code = code_match.group(1)
            capability = UnifiedCapability.query.filter(
                UnifiedCapability.code.ilike(f"{code}%")
            ).first()
            if capability:
                return capability

    # Stage 3: Partial name match (name contains search term)
    capability = UnifiedCapability.query.filter(
        UnifiedCapability.name.ilike(f"%{name}%")
    ).first()
    if capability:
        return capability

    # Stage 4: Fuzzy match using difflib (lowered cutoff from 0.8 to 0.6)
    all_caps = UnifiedCapability.query.with_entities(
        UnifiedCapability.id, UnifiedCapability.name
    ).all()

    if all_caps:
        cap_dict = {c.name: c.id for c in all_caps if c.name}
        matches = difflib.get_close_matches(name, cap_dict.keys(), n=1, cutoff=0.6)
        if matches:
            return UnifiedCapability.query.get(cap_dict[matches[0]])

    return None


def _link_application_to_capabilities(app, capabilities_str):
    """
    Link an application to capabilities (UnifiedCapability).

    Uses multi-stage matching to find capabilities:
    1. Exact name match
    2. Capability code match
    3. Partial name match
    4. Fuzzy match

    Args:
        app: ApplicationComponent instance
        capabilities_str: String of comma/semicolon separated capability names

    Returns:
        dict: {'linked': int, 'not_found': list, 'matched': list}
    """

    if not capabilities_str or not app:
        return {"linked": 0, "not_found": [], "matched": []}

    # Parse the capabilities string (supports comma, semicolon, pipe separators)
    capabilities = [
        c.strip() for c in re.split(r"[,;|]", capabilities_str) if c.strip()
    ]

    # Prefetch existing mappings to avoid N+1 query
    existing_mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
        application_component_id=app.id
    ).all()
    existing_mapping_keys = {
        mapping.unified_capability_id for mapping in existing_mappings
    }

    linked_count = 0
    not_found = []
    matched = []

    for cap_name in capabilities:
        # Use enhanced multi-stage matching
        capability = _find_capability_by_name_enhanced(cap_name)

        if capability:
            # Check if mapping already exists using prefetched set
            if capability.id not in existing_mapping_keys:
                # Create new mapping
                mapping = UnifiedApplicationCapabilityMapping(
                    application_component_id=app.id,
                    unified_capability_id=capability.id,
                    support_level="partial",  # Default support level
                    coverage_percentage=50,  # Default 50% coverage
                    relationship_type="enables",
                    notes=f"Auto-linked from import: {cap_name}",
                )
                db.session.add(mapping)
                existing_mapping_keys.add(
                    capability.id
                )  # Update set to prevent duplicates
                linked_count += 1
                matched.append({"input": cap_name, "matched_to": capability.name})
            else:
                matched.append(
                    {"input": cap_name, "matched_to": capability.name, "existing": True}
                )
        else:
            not_found.append(cap_name)

    return {"linked": linked_count, "not_found": not_found, "matched": matched}


def _suggest_process_links_semantic(app, confidence_threshold=0.5):
    """
    Suggest process links for an application using semantic matching.

    Analyzes application name and description to find matching processes.

    Args:
        app: ApplicationComponent instance
        confidence_threshold: Minimum similarity score (0 - 1) to suggest a link

    Returns:
        list: List of dicts with 'process', 'confidence', 'match_reason'
    """
    from difflib import SequenceMatcher


    if not app:
        return []

    suggestions = []

    # Get all active processes
    processes = BusinessProcess.query.filter(
        or_(BusinessProcess.status == "active", BusinessProcess.status.is_(None))
    ).all()

    # Build search text from application
    app_text = " ".join(
        filter(
            None,
            [
                app.name or "",
                app.description or "",
                app.business_purpose or "",
                app.notes or "",
            ],
        )
    ).lower()

    # Keywords from app name (split on spaces, underscores, camelCase)
    app_keywords = set(re.split(r"[\s_-]+|(?<=[a-z])(?=[A-Z])", app.name or ""))
    app_keywords = {kw.lower() for kw in app_keywords if len(kw) > 2}

    for process in processes:
        # Build process text
        process_text = " ".join(
            filter(
                None,
                [
                    process.name or "",
                    process.description or "",
                    process.process_code or "",
                ],
            )
        ).lower()

        # Process keywords
        process_keywords = set(
            re.split(r"[\s_-]+|(?<=[a-z])(?=[A-Z])", process.name or "")
        )
        process_keywords = {kw.lower() for kw in process_keywords if len(kw) > 2}

        # Calculate similarity scores
        # 1. Sequence similarity on full text
        seq_similarity = SequenceMatcher(
            None, app_text[:500], process_text[:500]
        ).ratio()

        # 2. Keyword overlap
        keyword_overlap = len(app_keywords & process_keywords) / max(
            len(app_keywords | process_keywords), 1
        )

        # 3. Name containment (check if process name is in app name or vice versa)
        name_match = 0
        if process.name and app.name:
            if (
                process.name.lower() in app.name.lower()
                or app.name.lower() in process.name.lower()
            ):
                name_match = 0.5

        # Combined confidence score (weighted average)
        confidence = (
            (seq_similarity * 0.3) + (keyword_overlap * 0.4) + (name_match * 0.3)
        )

        if confidence >= confidence_threshold:
            match_reasons = []
            if seq_similarity > 0.3:
                match_reasons.append(f"text similarity: {seq_similarity:.0%}")
            if keyword_overlap > 0.2:
                match_reasons.append(f"keyword overlap: {keyword_overlap:.0%}")
            if name_match > 0:
                match_reasons.append("name containment")

            suggestions.append(
                {
                    "process": process,
                    "process_id": process.id,
                    "process_name": process.name,
                    "process_code": process.process_code,
                    "confidence": round(confidence, 2),
                    "match_reason": ", ".join(match_reasons)
                    if match_reasons
                    else "general similarity",
                }
            )

    # Sort by confidence descending
    suggestions.sort(key=lambda x: x["confidence"], reverse=True)

    return suggestions[:10]  # Return top 10 suggestions


def validate_archimate_element_creation(
    name: str, archimate_type: str, layer: str, properties: dict = None
) -> dict:
    """
    Enhanced validation for ArchiMate element creation with layer-specific rules.

    Args:
        name: Element name
        archimate_type: ArchiMate element type
        layer: ArchiMate layer
        properties: Optional element properties

    Returns:
        dict: {'valid': bool, 'error': str}
    """
    # Basic validation
    if not name or not name.strip():
        return {"valid": False, "error": "Name is required"}

    if not archimate_type or not archimate_type.strip():
        return {"valid": False, "error": "ArchiMate type is required"}

    if not layer or not layer.strip():
        return {"valid": False, "error": "Layer is required"}

    # ArchiMate 3.2 Element Types by Layer
    element_types = {
        "motivation": [
            "Stakeholder",
            "Driver",
            "Assessment",
            "Goal",
            "Outcome",
            "Principle",
            "Requirement",
            "Constraint",
            "Meaning",
            "Value",
        ],
        "strategy": ["Resource", "Capability", "ValueStream", "CourseOfAction"],
        "business": [
            "BusinessActor",
            "BusinessRole",
            "BusinessCollaboration",
            "BusinessInterface",
            "BusinessProcess",
            "BusinessFunction",
            "BusinessInteraction",
            "BusinessEvent",
            "BusinessService",
            "BusinessObject",
            "Contract",
            "Representation",
            "Product",
        ],
        "application": [
            "ApplicationComponent",
            "ApplicationCollaboration",
            "ApplicationInterface",
            "ApplicationFunction",
            "ApplicationProcess",
            "ApplicationInteraction",
            "ApplicationEvent",
            "ApplicationService",
            "DataObject",
        ],
        "technology": [
            "Node",
            "Device",
            "SystemSoftware",
            "TechnologyCollaboration",
            "TechnologyInterface",
            "Path",
            "CommunicationNetwork",
            "TechnologyFunction",
            "TechnologyProcess",
            "TechnologyInteraction",
            "TechnologyEvent",
            "TechnologyService",
            "Artifact",
        ],
        "physical": ["Equipment", "Facility", "DistributionNetwork", "Material"],
        "implementation": [
            "WorkPackage",
            "Deliverable",
            "ImplementationEvent",
            "Plateau",
            "Gap",
        ],
    }

    # Validate layer
    layer = layer.lower()
    if layer not in element_types:
        return {
            "valid": False,
            "error": f"Invalid layer: {layer}. Must be one of: {list(element_types.keys())}",
        }

    # Validate element type for layer
    if archimate_type not in element_types[layer]:
        return {
            "valid": False,
            "error": f'Element type "{archimate_type}" is not valid for layer "{layer}". Valid types: {element_types[layer]}',
        }

    # Layer-specific validation (optional properties, not required)
    if layer == "motivation":
        # Motivation elements benefit from stakeholder or goal context
        if (
            archimate_type in ["Stakeholder", "Goal"]
            and properties
            and len(properties) > 0
        ):
            # Properties provided but not required
            pass

    elif layer == "strategy":
        # Strategy elements benefit from business value context
        if archimate_type == "Capability" and properties and len(properties) > 0:
            # Properties provided but not required
            pass

    elif layer == "business":
        # Business elements benefit from process context
        if (
            archimate_type in ["BusinessProcess", "BusinessFunction"]
            and properties
            and len(properties) > 0
        ):
            # Properties provided but not required
            pass

    elif layer == "application":
        # Application elements benefit from technical specifications
        if (
            archimate_type == "ApplicationComponent"
            and properties
            and len(properties) > 0
        ):
            # Properties provided but not required
            pass

    elif layer == "technology":
        # Technology elements benefit from infrastructure details
        if archimate_type in ["Node", "Device"] and properties and len(properties) > 0:
            # Properties provided but not required
            pass

    return {"valid": True, "error": ""}


from sqlalchemy.exc import SQLAlchemyError

# Import statements (moved to proper location)
from ..models.business_capabilities import BusinessCapability, BusinessFunction
from ..models.business_layer import (
    BusinessActor,
    BusinessObject,
    BusinessRole,
    BusinessService,
)
from ..models.implementation_migration import Deliverable
from ..models.implementation_migration import Plateau
from ..models.implementation_migration import WorkPackage
from ..models.models import Requirement
from ..models.physical_layer import (
    PhysicalDistributionNetwork,
    PhysicalEquipment,
    PhysicalFacility,
    PhysicalMaterial,
)
from ..models.process_data import BusinessProcess
from ..models.relationship_tables import ApplicationProcessSupport
from ..models.technology_layer import (
    Device,
    Node,
    SystemSoftware,
)
from ..models.unified_application_capability_mapping import (
    UnifiedApplicationCapabilityMapping,
)
from ..models.vendor.vendor_organization import (
    VendorOrganization,
    VendorProduct,
    application_vendor_products,
)
from ..services.archimate_validation_service import ArchiMateValidationService

# ============================================================================
# Batch Query Helpers - N + 1 Query Prevention
# ============================================================================


def get_archimate_elements_batch(application_component_id, layer=None, search=None):
    """
    Fetch all ArchiMate elements for an application in optimized batch queries.

    Instead of 21+ sequential queries (one per element type), this uses a single
    query per layer group, dramatically reducing database round trips.

    Args:
        application_component_id: The application component ID to query
        layer: Optional layer filter (motivation, business, application, technology, physical, implementation)
        search: Optional search string for name/description filtering

    Returns:
        List of serialized element dictionaries
    """
    elements = []
    search_lower = search.lower() if search else None

    def matches_search(elem):
        """Check if element matches search criteria."""
        if not search_lower:
            return True
        return (
            search_lower in (elem.name or "").lower()
            or search_lower
            in (getattr(elem, "description", None) or "").lower()  # model-safety-ok
        )

    def serialize_element(elem, elem_type, layer_name):
        """Serialize an element to dictionary format."""
        return {
            "id": elem.id,
            "name": elem.name,
            "archimate_type": elem_type,
            "layer": layer_name,
            "description": getattr(
                elem, "description", None
            ),  # model-safety-ok: polymorphic element attribute,
            "code": getattr(
                elem, "code", None
            ),  # model-safety-ok: polymorphic element attribute,
            "framework": getattr(
                elem, "framework", None
            ),  # model-safety-ok: polymorphic element attribute,
            "category": getattr(
                elem, "category", None
            ),  # model-safety-ok: polymorphic element attribute,
            "properties": {},
            "created_at": elem.created_at.isoformat()
            if elem.created_at  # model-safety-ok: direct attribute check
            else None,
            "model_type": "archimate",
        }

    # Define element types per layer for batch querying
    layer_config = {
        "motivation": [
            (Goal, "Goal"),
            (Driver, "Driver"),
            (Requirement, "Requirement"),
        ],
        "business": [
            (BusinessActor, "BusinessActor"),
            (BusinessRole, "BusinessRole"),
            (BusinessService, "BusinessService"),
            (BusinessFunction, "BusinessFunction"),
            (BusinessObject, "BusinessObject"),
        ],
        "application": [
            (ApplicationInterface, "ApplicationInterface"),
            (ApplicationService, "ApplicationService"),
            (DataObject, "DataObject"),
        ],
        "technology": [
            (Node, "Node"),
            (Device, "Device"),
            (SystemSoftware, "SystemSoftware"),
        ],
        "physical": [
            (PhysicalEquipment, "PhysicalEquipment"),
            (PhysicalFacility, "PhysicalFacility"),
            (PhysicalDistributionNetwork, "PhysicalDistributionNetwork"),
            (PhysicalMaterial, "PhysicalMaterial"),
        ],
        "implementation": [
            (WorkPackage, "WorkPackage"),
            (Deliverable, "Deliverable"),
            (Plateau, "Plateau"),
        ],
    }

    # Determine which layers to query
    layers_to_query = (
        [layer] if layer and layer in layer_config else list(layer_config.keys())
    )

    # Batch query each layer
    for layer_name in layers_to_query:
        if layer_name not in layer_config:
            continue

        for model_class, type_name in layer_config[layer_name]:
            try:
                # Single query per model type - all elements for this application
                model_elements = model_class.query.filter_by(  # model-safety-ok: iterating over model classes, not data rows
                    application_component_id=application_component_id
                ).all()

                for elem in model_elements:
                    if matches_search(elem):
                        elements.append(serialize_element(elem, type_name, layer_name))
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                # Model may not have the expected column or other issues
                logger.debug(f"Model error: {e}")

    return elements


def get_element_counts_batch(application_component_id):
    """
    Get counts of ArchiMate elements by layer using optimized batch queries.

    Instead of 21+ individual COUNT queries, this groups counts by layer
    and uses fewer database round trips.

    Args:
        application_component_id: The application component ID to query

    Returns:
        Dictionary with element counts per layer
    """
    counts = {
        "strategy": 0,
        "motivation": 0,
        "business": 0,
        "application": 0,
        "technology": 0,
        "physical": 0,
        "implementation": 0,
    }

    # Define models per layer for batch counting
    layer_models = {
        "motivation": [Goal, Driver, Requirement],
        "business": [
            BusinessActor,
            BusinessRole,
            BusinessService,
            BusinessFunction,
            BusinessObject,
        ],
        "application": [ApplicationInterface, ApplicationService, DataObject],
        "technology": [Node, Device, SystemSoftware],
        "physical": [
            PhysicalEquipment,
            PhysicalFacility,
            PhysicalDistributionNetwork,
            PhysicalMaterial,
        ],
        "implementation": [WorkPackage, Deliverable, Plateau],
    }

    # Batch count for each layer
    for layer_name, models in layer_models.items():
        layer_total = 0
        for model_class in models:
            try:
                layer_total += model_class.query.filter_by(  # model-safety-ok: iterating over model classes, not data rows
                    application_component_id=application_component_id
                ).count()
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug(
                    "Optional model query failed for layer %s: %s", layer_name, e
                )
        counts[layer_name] = layer_total

    return counts


# ============================================================================
# Blueprint Error Handlers - Transaction Management
# ============================================================================


@application_mgmt.before_request
def before_request():
    """
    Ensure database session is in clean state before each request.
    Rolls back any lingering failed transactions.
    """
    if not db.session.is_active:
        try:
            db.session.rollback()
        except Exception:
            db.session.remove()


@application_mgmt.teardown_request
def teardown_request(exception=None):
    """
    Ensure database session is properly cleaned up after each request.
    Rolls back any failed transactions to prevent 'transaction aborted' errors.
    Skips db.session.remove() in testing mode to prevent DetachedInstanceError
    caused by Flask-Login's cached current_user being detached across requests.
    """
    if exception:
        try:
            db.session.rollback()
        except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.debug(f"Ignored: {e}")
    if not current_app.testing:
        try:
            db.session.remove()
        except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
            logger.debug(f"Ignored: {e}")


@application_mgmt.errorhandler(SQLAlchemyError)
def handle_db_error(error):
    """
    Handle all SQLAlchemy errors by rolling back the transaction.
    This prevents 'current transaction is aborted' errors.
    """
    db.session.rollback()
    current_app.logger.error(f"Database error: {str(error)}")

    # If it's an AJAX request, return JSON
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Database error occurred. Please try again.",
                }
            ),
            500,
        )

    # Otherwise, flash message and redirect
    flash("A database error occurred. Please try again.", "danger")
    return redirect(request.referrer or url_for("main.index"))


CAPABILITY_SUPPORT_LEVEL_CHOICES = [
    ("primary", "Primary"),
    ("secondary", "Secondary"),
    ("partial", "Partial"),
    ("planned", "Planned"),
    ("legacy", "Legacy"),
]

CAPABILITY_MATURITY_CHOICES = [(str(level), f"Level {level}") for level in range(1, 6)]

ARCHIMATE_RELATIONSHIP_CHOICES = [
    ("assignment", "Assignment"),
    ("serving", "Serving"),
    ("access", "Access"),
    ("association", "Association"),
]


def _redirect_to_detail(app_id, tab=None, **kwargs):
    """Redirect to application detail preserving tab context."""
    url = url_for("unified_applications.application_detail", id=app_id, **kwargs)
    if tab:
        sep = "&" if "?" in url else "?"
        url += f"{sep}tab={tab}"
    return redirect(url)


def _add_archimate_element(
    app_id, layer, element_type, data, rel_type="realization", reverse_rel=False
):
    """Generic helper to add ArchiMate elements for layer editing."""
    try:
        app = ApplicationComponent.query.get_or_404(app_id)

        if not app.archimate_element_id:
            return (
                jsonify({"success": False, "error": "Application not linked to ArchiMate element"}),
                400,
            )

        app_element = db.session.get(ArchiMateElement, app.archimate_element_id)
        if not app_element or not app_element.architecture_id:
            return jsonify({"success": False, "error": "No architecture found"}), 400

        element_name = (data.get("name") or "").strip()
        if not element_name:
            return jsonify({"success": False, "error": "Element name is required"}), 400

        description = (data.get("description") or "").strip() or None
        properties = data.get("properties", {}) or {}

        new_element = ArchiMateElement(
            name=element_name,
            description=description,
            type=element_type,
            layer=layer,
            architecture_id=app_element.architecture_id,
            properties=properties if isinstance(properties, dict) else {},
        )

        db.session.add(new_element)
        db.session.flush()

        if reverse_rel:
            source_id = new_element.id
            target_id = app_element.id
        else:
            source_id = app_element.id
            target_id = new_element.id

        from flask_login import current_user

        is_valid, error_msg, rule = ArchiMateValidationService.validate_and_log(
            source_element_id=source_id,
            target_element_id=target_id,
            relationship_type=rel_type,
            user_id=current_user.id if hasattr(current_user, "id") else None,
            severity="warning",
        )

        if not is_valid:
            db.session.rollback()
            return jsonify({"success": False, "error": error_msg}), 400

        relationship = ArchiMateRelationship(
            type=rel_type,
            source_id=source_id,
            target_id=target_id,
            architecture_id=app_element.architecture_id,
        )

        db.session.add(relationship)
        db.session.commit()

        return jsonify({
            "success": True,
            "element": {
                "id": new_element.id,
                "name": new_element.name,
                "type": new_element.type,
                "layer": new_element.layer,
                "description": new_element.description,
            },
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error adding ArchiMate element: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


def _delete_archimate_element(element_id, rel_type="realization"):
    """Generic helper to delete ArchiMate elements for layer editing.

    Uses raw SQL with savepoints to comprehensively clear all FK references
    before deleting the element.  Every NO ACTION FK on archimate_elements is
    handled here — nullable columns are NULLed, non-nullable rows are deleted.
    CASCADE / SET NULL constraints are handled automatically by the DB.
    """
    try:
        element = ArchiMateElement.query.get_or_404(element_id)

        eid = int(element_id)

        # Tenant guard: never let one org delete another org's element graph.
        # Query.get_or_404 can be served from the identity map and bypass the
        # ORM tenant filter, so assert ownership against the actual column before
        # the destructive cascade. No-op in system contexts (no tenant); legacy
        # rows with no organization_id are left deletable.
        from flask import abort as _abort, g as _g
        _org = getattr(_g, "current_org_id", None)
        if _org is not None:
            _owner = db.session.execute(
                text("SELECT organization_id FROM archimate_elements WHERE id = :id"),
                {"id": eid},
            ).scalar()
            if _owner is not None and _owner != _org:
                _abort(404)

        # Every statement below is keyed on `eid`, and `eid` is `int(element_id)`
        # for an element whose `organization_id` was just read straight from the
        # table and compared against `g.current_org_id` above. Re-scoping the
        # cascade by organisation would be worse than useless: it would leave a
        # row in another organisation still pointing at the element being
        # deleted, and the NO ACTION foreign keys these statements exist to
        # clear would then abort the delete.
        #
        # ------------------------------------------------------------------
        # 1. Self-referential: clear children that point to this as parent/template
        # ------------------------------------------------------------------
        # tenancy-ok: keyed on eid, whose organization_id was asserted against
        # g.current_org_id immediately above; scoping the cascade would strand FKs
        db.session.execute(
            text("UPDATE archimate_elements SET parent_id = NULL WHERE parent_id = :id"),
            {"id": eid},
        )
        # tenancy-ok: keyed on the same ownership-checked eid
        db.session.execute(
            text("UPDATE archimate_elements SET template_element_id = NULL WHERE template_element_id = :id"),
            {"id": eid},
        )

        # ------------------------------------------------------------------
        # 2. archimate_relationships — delete ALL relationships involving this element
        #    (the old code only deleted rows matching a single rel_type, which
        #    left other relationship types in place and caused FK violations)
        # ------------------------------------------------------------------
        # saved_diagram_relationships references archimate_relationships, so
        # delete those first before deleting the relationships themselves.
        # tenancy-ok: keyed on the same ownership-checked eid
        db.session.execute(
            text("""
                DELETE FROM saved_diagram_relationships
                WHERE relationship_id IN (
                    SELECT id FROM archimate_relationships
                    WHERE source_id = :id OR target_id = :id
                )
            """),
            {"id": eid},
        )
        # tenancy-ok: keyed on the same ownership-checked eid. A relationship
        # touching this element must go regardless of which organisation owns the
        # relationship row, or the element delete fails on its FK.
        db.session.execute(
            text("DELETE FROM archimate_relationships WHERE source_id = :id OR target_id = :id"),
            {"id": eid},
        )

        # ------------------------------------------------------------------
        # 3. other_relationships
        # ------------------------------------------------------------------
        db.session.execute(
            text("DELETE FROM other_relationships WHERE source_id = :id OR target_id = :id"),
            {"id": eid},
        )

        # ------------------------------------------------------------------
        # 4. Rows that MUST be deleted (non-nullable FK, cannot be NULLed)
        # ------------------------------------------------------------------
        # application_interface_metadata
        db.session.execute(
            text("DELETE FROM application_interface_metadata WHERE archimate_element_id = :id"),
            {"id": eid},
        )
        # relationship_suggestions (both columns are NOT NULL)
        db.session.execute(
            text("DELETE FROM relationship_suggestions WHERE source_element_id = :id OR target_element_id = :id"),
            {"id": eid},
        )
        # saved_diagram_elements
        db.session.execute(
            text("DELETE FROM saved_diagram_elements WHERE element_id = :id"),
            {"id": eid},
        )
        # system_dependencies: source_system_id and target_system_id are NOT NULL
        db.session.execute(
            text("DELETE FROM system_dependencies WHERE source_system_id = :id OR target_system_id = :id"),
            {"id": eid},
        )
        # system_deployments: system_id is NOT NULL
        db.session.execute(
            text("DELETE FROM system_deployments WHERE system_id = :id"),
            {"id": eid},
        )
        # system_hierarchies: both columns are NOT NULL
        db.session.execute(
            text("DELETE FROM system_hierarchies WHERE parent_system_id = :id OR child_system_id = :id"),
            {"id": eid},
        )
        # system_lifecycles: system_id is NOT NULL
        db.session.execute(
            text("DELETE FROM system_lifecycles WHERE system_id = :id"),
            {"id": eid},
        )
        # data_object_storage: application_component_id is NOT NULL
        # tenancy-ok: keyed on the same ownership-checked eid
        db.session.execute(
            text("DELETE FROM data_object_storage WHERE application_component_id = :id"),
            {"id": eid},
        )
        # interface_consumer: consumer_application_id is NOT NULL
        db.session.execute(
            text("DELETE FROM interface_consumer WHERE consumer_application_id = :id"),
            {"id": eid},
        )

        # ------------------------------------------------------------------
        # 5. Nullable FK columns — NULL them out rather than deleting the
        #    parent entity (e.g. do not delete a Solution just because its
        #    archimate_element_id is being removed).
        # ------------------------------------------------------------------
        _nullable_refs = [
            # (table, column)
            ("application_capability",               "archimate_application_id"),
            ("application_collaborations",           "archimate_element_id"),
            ("application_data_objects",             "archimate_element_id"),
            ("application_events",                   "archimate_element_id"),
            ("application_events",                   "publisher_application_id"),
            ("application_functions",                "archimate_element_id"),
            ("application_interactions",             "archimate_element_id"),
            ("application_interfaces",               "archimate_element_id"),
            ("application_interfaces",               "provider_application_id"),
            ("application_processes",                "archimate_element_id"),
            ("application_services",                 "archimate_element_id"),
            ("archimate_contracts",                  "archimate_element_id"),
            ("archimate_representations",            "archimate_element_id"),
            ("archimate_resources",                  "archimate_element_id"),
            ("architecture_review_findings",         "element_id"),
            ("assessments",                          "archimate_element_id"),
            ("business_actors",                      "archimate_element_id"),
            ("business_capability",                  "archimate_element_id"),
            ("business_collaborations",              "archimate_element_id"),
            ("business_events",                      "archimate_element_id"),
            ("business_function",                    "archimate_element_id"),
            ("business_interactions",                "archimate_element_id"),
            ("business_interfaces",                  "archimate_element_id"),
            ("business_objects",                     "archimate_element_id"),
            ("business_objects",                     "master_system_id"),
            ("business_processes",                   "archimate_element_id"),
            ("business_roles",                       "archimate_element_id"),
            ("business_services",                    "archimate_element_id"),
            ("capability_archimate_classifications", "archimate_element_id"),
            ("code_artifacts",                       "source_element_id"),
            ("compliance_requirements",              "archimate_element_id"),
            ("composite_structures",                 "child_element_id"),
            ("composite_structures",                 "parent_element_id"),
            ("conceptual_data_models",               "archimate_element_id"),
            ("constraints",                          "archimate_element_id"),
            ("constraints",                          "goal_id"),
            ("courses_of_action",                    "archimate_element_id"),
            ("data_catalogs",                        "archimate_element_id"),
            ("data_domains",                         "archimate_element_id"),
            ("data_entities",                        "archimate_element_id"),
            ("data_lineage",                         "archimate_element_id"),
            ("data_transformations",                 "archimate_element_id"),
            ("design_patterns",                      "archimate_element_id"),
            ("drivers",                              "archimate_element_id"),
            ("equipment",                            "archimate_element_id"),
            ("functional_requirement",               "archimate_element_id"),
            ("goals",                                "archimate_element_id"),
            ("logical_data_models",                  "archimate_element_id"),
            ("manufacturing_plants",                 "archimate_element_id"),
            ("meanings",                             "archimate_element_id"),
            ("missing_business_collaborations",      "archimate_element_id"),
            ("missing_business_interactions",        "archimate_element_id"),
            ("missing_business_interfaces",          "archimate_element_id"),
            ("motivation_drivers",                   "archimate_element_id"),
            ("motivation_goals",                     "archimate_element_id"),
            ("motivation_principles",                "archimate_element_id"),
            ("non_functional_requirement",           "archimate_element_id"),
            ("outcomes",                             "archimate_element_id"),
            ("outcomes",                             "goal_id"),
            ("physical_data_models",                 "archimate_element_id"),
            ("physical_distribution_networks",       "archimate_element_id"),
            ("physical_equipment",                   "archimate_element_id"),
            ("physical_facilities",                  "archimate_element_id"),
            ("physical_materials",                   "archimate_element_id"),
            ("portfolio_initiatives",                "archimate_element_id"),
            ("principles",                           "archimate_element_id"),
            ("production_lines",                     "archimate_element_id"),
            ("products",                             "archimate_element_id"),
            ("project_constraints",                  "archimate_element_id"),
            ("quality_attributes",                   "archimate_element_id"),
            ("representations",                      "archimate_element_id"),
            ("requirements",                         "archimate_element_id"),
            ("requirements",                         "driver_id"),
            ("requirements",                         "goal_id"),
            ("requirements",                         "source_element_id"),
            ("requirements",                         "stakeholder_id"),
            ("risk_assessments",                     "archimate_element_id"),
            ("risk_assessments",                     "driver_id"),
            ("risk_assessments",                     "goal_id"),
            ("software_dependencies",                "archimate_element_id"),
            ("software_modules",                     "archimate_element_id"),
            ("solution_compliance_mappings",         "archimate_element_id"),
            ("solution_contracts_model",             "archimate_element_id"),
            ("solution_governance_exceptions",       "principle_id"),
            ("solution_patterns",                    "archimate_element_id"),
            ("solution_quality_attributes",          "constraint_id"),
            ("solution_quality_attributes",          "principle_id"),
            ("solutions",                            "archimate_element_id"),
            ("stakeholders",                         "archimate_element_id"),
            ("strategy_resources",                   "archimate_element_id"),
            ("structural_groupings",                 "archimate_element_id"),
            ("structural_junctions",                 "archimate_element_id"),
            ("structural_locations",                 "archimate_element_id"),
            ("system_boundaries",                    "archimate_element_id"),
            ("system_dependencies",                  "interface_id"),
            ("system_interfaces",                    "archimate_element_id"),
            ("system_interfaces",                    "source_system_id"),
            ("system_interfaces",                    "target_system_id"),
            ("system_lifecycles",                    "replacement_system_id"),
            ("technology_artifacts",                 "archimate_element_id"),
            ("technology_collaborations",            "archimate_element_id"),
            ("technology_collaborations_full",       "archimate_element_id"),
            ("technology_communication_networks",    "archimate_element_id"),
            ("technology_devices",                   "archimate_element_id"),
            ("technology_events",                    "archimate_element_id"),
            ("technology_functions",                 "archimate_element_id"),
            ("technology_interactions",              "archimate_element_id"),
            ("technology_interfaces",                "archimate_element_id"),
            ("technology_nodes",                     "archimate_element_id"),
            ("technology_paths",                     "archimate_element_id"),
            ("technology_processes",                 "archimate_element_id"),
            ("technology_services",                  "archimate_element_id"),
            ("technology_system_software",           "archimate_element_id"),
            ("traceability_links",                   "source_archimate_element_id"),
            ("traceability_links",                   "target_archimate_element_id"),
            ("uml_elements",                         "archimate_element_id"),
            ("unified_capabilities",                 "archimate_element_id"),
            ("values",                               "archimate_element_id"),
        ]
        for table, col in _nullable_refs:
            db.session.execute(
                text(f"UPDATE {table} SET {col} = NULL WHERE {col} = :id"),  # noqa: S608
                {"id": eid},
            )

        # ------------------------------------------------------------------
        # 6. Finally delete the element itself (CASCADE constraints in DB will
        #    clean up: application_vendor_products, business_function_applications,
        #    data_lineage_flows, element_template_usage, gap_archimate_elements,
        #    grouping_elements, plateau_archimate_elements, solution_elements,
        #    workflow_instance_archimate_elements)
        # ------------------------------------------------------------------
        db.session.delete(element)
        db.session.commit()

        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting ArchiMate element: {str(e)}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


VENDOR_DEPLOYMENT_CHOICES = [
    ("primary_system", "Primary System"),
    ("integration_layer", "Integration Layer"),
    ("data_source", "Data Source"),
    ("reporting", "Reporting/Analytics"),
]

VENDOR_CRITICALITY_CHOICES = [
    ("mission_critical", "Mission Critical"),
    ("business_critical", "Business Critical"),
    ("important", "Important"),
    ("supporting", "Supporting"),
]

VENDOR_HOSTING_CHOICES = [
    ("cloud", "Cloud"),
    ("saas", "SaaS"),
    ("hybrid", "Hybrid"),
    ("on_premise", "On-Premise"),
]


def _build_vendor_product_choices(include_ids=None):
    include_ids = set(include_ids or [])

    vendor_products = (
        VendorProduct.query.outerjoin(
            VendorOrganization, VendorProduct.vendor_organization
        )
        .options(joinedload(VendorProduct.vendor_organization))
        .order_by(
            VendorOrganization.display_name.asc(),
            VendorOrganization.name.asc(),
            VendorProduct.name.asc(),
        )
        .all()
    )

    choices = []
    seen_ids = set()

    for product in vendor_products:
        organization = product.vendor_organization
        org_label = (
            organization.display_name or organization.name
            if organization
            else "Unknown Vendor"
        )
        choices.append((product.id, f"{org_label} • {product.name}"))
        seen_ids.add(product.id)

    missing_ids = include_ids - seen_ids
    if missing_ids:
        fallback_products = (
            VendorProduct.query.filter(VendorProduct.id.in_(missing_ids))
            .options(joinedload(VendorProduct.vendor_organization))
            .all()
        )
        for product in fallback_products:
            organization = product.vendor_organization
            org_label = (
                organization.display_name or organization.name
                if organization
                else "Unknown Vendor"
            )
            choices.append((product.id, f"{org_label} • {product.name}"))

    return choices


def _list_vendor_product_ids(archimate_element_id):
    if not archimate_element_id:
        return []
    result = db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id)
        select(application_vendor_products.c.vendor_product_id).where(
            application_vendor_products.c.archimate_element_id == archimate_element_id
        )
    )
    return [row[0] for row in result]


def _sync_vendor_products(archimate_element_id, target_ids):
    table = application_vendor_products
    existing_rows = db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id)
        select(table.c.vendor_product_id).where(
            table.c.archimate_element_id == archimate_element_id
        )
    )
    existing_ids = {row[0] for row in existing_rows}
    target_ids = {int(value) for value in target_ids if value}

    to_remove = existing_ids - target_ids
    to_add = target_ids - existing_ids

    if to_remove:
        db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id)
            table.delete().where(
                table.c.archimate_element_id == archimate_element_id,
                table.c.vendor_product_id.in_(to_remove),
            )
        )

    for vendor_product_id in to_add:
        db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
            table.insert().values(
                archimate_element_id=archimate_element_id,
                vendor_product_id=vendor_product_id,
            )
        )


def render_application_detail(id):
    """Render the Application Detail page.

    Called by unified_applications_bp (canonical /applications/<id>).
    Restored after BE-054 god-file decomposition dropped this shared handler.
    """
    from app.models.application_portfolio import ApplicationComponent

    app_obj = ApplicationComponent.query.get_or_404(id)

    # --- Capabilities ---
    # Query ApplicationCapabilityMapping (populated by Abacus sync — 410 rows)
    # NOT UnifiedApplicationCapabilityMapping (empty table, different schema)
    capabilities = []
    capability_mappings = []
    try:
        from app.models.application_capability import ApplicationCapabilityMapping
        # tenant-scoping-ok: filtered by application_component_id == id below,
        # and app_obj (id) is an org-scoped ApplicationComponent load.
        cap_pairs = (
            db.session.query(ApplicationCapabilityMapping, BusinessCapability)
            .join(
                BusinessCapability,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            )
            .filter(ApplicationCapabilityMapping.application_component_id == id)
            .all()
        )
        capabilities = [cap for _, cap in cap_pairs]
        capability_mappings = cap_pairs  # Keep (mapping, cap) pairs for table

        # DEF-003 follow-up (3 Sep 2026): the "Map Capability" modal's create
        # handler (application_capability_mapping_create,
        # detail_layer_routes.py) writes UnifiedApplicationCapabilityMapping —
        # the canonical store per ADR-0008 — but this display only ever read
        # the legacy ApplicationCapabilityMapping table above. A capability
        # mapped through the modal therefore persisted correctly (confirmed
        # in the DB) yet never appeared here: silent, not a crash. Rather than
        # migrate the legacy rows into the unified table (a locked, checksum-
        # verified data operation matching project_capabilities.py's rigor
        # that this fix does not attempt), the display unions both sources,
        # de-duplicated by the underlying BusinessCapability identity so an
        # already-legacy-linked capability cannot render twice.
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )
        from app.models.unified_capability import UnifiedCapability

        legacy_capability_ids = {cap.id for cap in capabilities}
        unified_pairs = (
            db.session.query(UnifiedApplicationCapabilityMapping, UnifiedCapability)
            .join(
                UnifiedCapability,
                UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
            )
            .filter(
                UnifiedApplicationCapabilityMapping.application_component_id == id,
                UnifiedCapability.source_table == "business_capability",
            )
            .all()
        )
        for u_mapping, u_cap in unified_pairs:
            try:
                source_bc_id = int(u_cap.source_id)
            except (TypeError, ValueError):
                continue
            if source_bc_id in legacy_capability_ids:
                continue
            legacy_capability_ids.add(source_bc_id)
            capability_mappings.append((u_mapping, u_cap))
            capabilities.append(u_cap)
    except Exception:
        db.session.rollback()

    # --- Capability form config (for "Map Capability" modal) ---
    existing_cap_ids = {c.id for c in capabilities}
    capability_options = []
    try:
        all_caps = BusinessCapability.query.order_by(BusinessCapability.name.asc()).all()
        for cap in all_caps:
            capability_options.append({
                "id": cap.id,
                "label": f"{cap.name} \u00b7 {cap.category or 'Uncategorized'}",
                "disabled": cap.id in existing_cap_ids,
            })
    except Exception:
        db.session.rollback()
    capability_form_config = {
        "capability_options": capability_options,
        "support_levels": [
            ("primary", "Primary"), ("secondary", "Secondary"),
            ("partial", "Partial"), ("legacy", "Legacy"),
            ("planned", "Planned"), ("linked", "Linked"),
        ],
        "maturity_levels": [(str(i), f"Level {i}") for i in range(1, 6)],
    }

    # --- Requirements ---
    requirements = []
    try:
        from app.models.requirements import Requirement
        from app.models.relationship_tables import ApplicationRequirementMapping

        requirements = (
            db.session.query(Requirement)
            .join(
                ApplicationRequirementMapping,
                Requirement.id == ApplicationRequirementMapping.requirement_id,
            )
            .filter(ApplicationRequirementMapping.application_component_id == id)
            .all()
        )
    except Exception:
        db.session.rollback()

    # --- Interfaces ---
    interfaces = []
    try:
        interfaces = ApplicationInterface.query.filter_by(
            provider_application_id=app_obj.id
        ).all()
    except Exception:
        db.session.rollback()

    # --- Linked Solutions ---
    linked_solutions = []
    try:
        from app.models.solution_models import Solution, solution_applications
        linked_solutions = (
            db.session.query(Solution)
            .join(solution_applications, Solution.id == solution_applications.c.solution_id)
            .filter(solution_applications.c.application_component_id == id)
            .all()
        )
    except Exception:
        db.session.rollback()

    # --- ArchiMate element info ---
    archimate_info = None
    if app_obj.archimate_element_id:
        try:
            from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
            ae = db.session.get(ArchiMateElement, app_obj.archimate_element_id)
            if ae:
                rel_count = ArchiMateRelationship.query.filter(
                    db.or_(
                        ArchiMateRelationship.source_id == ae.id,
                        ArchiMateRelationship.target_id == ae.id,
                    )
                ).count()
                from app.models.solution_archimate_element import SolutionArchiMateElement
                sol_count = SolutionArchiMateElement.query.filter_by(element_id=ae.id).count()
                archimate_info = {
                    "id": ae.id,
                    "name": ae.name,
                    "type": ae.type,
                    "layer": ae.layer,
                    "rel_count": rel_count,
                    "sol_count": sol_count,
                }
        except Exception:
            db.session.rollback()

    # --- ArchiMate relationships for this app ---
    archimate_relationships = {'incoming': [], 'outgoing': []}
    if app_obj.archimate_element_id:
        try:
            from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
            ae_id = app_obj.archimate_element_id

            outgoing = db.session.query(ArchiMateRelationship, ArchiMateElement).join(
                ArchiMateElement, ArchiMateRelationship.target_id == ArchiMateElement.id
            ).filter(ArchiMateRelationship.source_id == ae_id).all()

            incoming = db.session.query(ArchiMateRelationship, ArchiMateElement).join(
                ArchiMateElement, ArchiMateRelationship.source_id == ArchiMateElement.id
            ).filter(ArchiMateRelationship.target_id == ae_id).all()

            for rel, elem in outgoing:
                archimate_relationships['outgoing'].append({
                    'id': elem.id, 'name': elem.name, 'type': elem.type,
                    'layer': (elem.layer or '').title(), 'rel_type': rel.type,
                })
            for rel, elem in incoming:
                archimate_relationships['incoming'].append({
                    'id': elem.id, 'name': elem.name, 'type': elem.type,
                    'layer': (elem.layer or '').title(), 'rel_type': rel.type,
                })
        except Exception:
            db.session.rollback()

    # --- Vendor summary ---
    vendor_footprint = []
    vendor_summary = {
        "vendor_count": 0,
        "product_count": 0,
        "has_archimate": bool(app_obj.archimate_element_id),
    }
    # Populate vendor product dropdown from VendorProduct table
    all_products = []
    try:
        vp_rows = (
            db.session.query(VendorProduct.id, VendorProduct.name, VendorOrganization.name.label("vendor_name"))
            .join(VendorOrganization, VendorProduct.vendor_organization_id == VendorOrganization.id)
            .order_by(VendorOrganization.name, VendorProduct.name)
            .all()
        )
        all_products = [{"id": r.id, "label": f"{r.vendor_name} \u2014 {r.name}"} for r in vp_rows]
    except Exception:
        db.session.rollback()
    vendor_form_config = {
        "linked_products": [],
        "all_products": all_products,
        "deployment_types": VENDOR_DEPLOYMENT_CHOICES,
        "criticalities": VENDOR_CRITICALITY_CHOICES,
        "hosting_models": VENDOR_HOSTING_CHOICES,
    }

    # --- Metric cards ---
    metric_cards = [
        {
            # Labelled for the field it actually plots. It read "Lifecycle
            # Stage" while showing deployment_status, directly above an
            # "Application Identity / Lifecycle Status" row showing
            # lifecycle_status -- so the same page answered "what stage is this
            # application at?" twice, with two different values, and neither
            # label said which field it came from.
            #
            # An unrecorded status is an em dash, not the word "unknown"
            # (and previously not the ORM default "development", which read as
            # a recorded answer).
            "label": "Deployment Status",
            "value": (
                app_obj.deployment_status.replace("_", " ").title()
                if app_obj.deployment_status
                else "—"
            ),
            "delta": (
                f"Criticality: {app_obj.business_criticality}"
                if app_obj.business_criticality
                else "Criticality not recorded"
            ),
            "icon_svg": "",
        },
        {
            "label": "Interfaces Linked",
            "value": str(len(interfaces)),
            "delta": "Exposed integrations connected to this component",
            "icon_svg": "",
        },
        {
            "label": "Mapped Capabilities",
            "value": str(len(capabilities)),
            "delta": "Enterprise capabilities linked to this application",
            "icon_svg": "",
        },
        {
            "label": "Linked Requirements",
            "value": str(len(requirements)),
            "delta": "Requirements implemented by this component",
            "icon_svg": "",
        },
    ]

    # --- Dashboard config (empty tables for display) ---
    def _empty_table():
        return {
            "filterKey": "name",
            "sortableColumns": [],
            "defaultPageSize": 5,
            "columns": [],
            "rows": [],
        }

    # Build capabilities table from the queried data (with mapping fields)
    cap_table = _empty_table()
    if capability_mappings:
        cap_table["columns"] = ["Capability", "Category", "Support", "Coverage", "Maturity", "Strategic", "Compliance"]
        cap_rows = []
        for mapping, cap in capability_mappings:
            # DEF-003 follow-up: `mapping` is either an ApplicationCapabilityMapping
            # (legacy) or a UnifiedApplicationCapabilityMapping (canonical, from the
            # union added above) \u2014 they share support_level/coverage_percentage but
            # not maturity_contribution_score, compliance_level, or is_primary_enabler.
            # getattr(..., None/False) rather than a plain attribute access, so a
            # unified-sourced row renders with an honest \u2014 em dash instead of a 500.
            support = mapping.support_level or ""
            coverage_pct = mapping.coverage_percentage
            maturity = cap.current_maturity_level
            if maturity is None:
                maturity = getattr(mapping, "maturity_contribution_score", None)
            if maturity is None:
                maturity = getattr(mapping, "maturity_level", None)
            compliance = getattr(mapping, "compliance_level", None) or ""
            is_strategic = getattr(mapping, "is_primary_enabler", None)
            if is_strategic is None:
                is_strategic = getattr(mapping, "is_strategic", False)
            cap_rows.append({
                "name": cap.name,
                "detail_url": None,
                "category": cap.category or "",
                "support_level": support.replace("_", " ").title() if support else "",
                "coverage": f"{coverage_pct}%" if coverage_pct is not None else "",
                "maturity": f"L{maturity}" if maturity is not None else "",
                "strategic": "Yes" if is_strategic else "No",
                "compliance": compliance.replace("_", " ").title() if compliance else "\u2014",
            })
        cap_table["rows"] = cap_rows

    dashboard_config = {
        "charts": {},
        "tables": {
            "application-relationships": _empty_table(),
            "application-capabilities": cap_table,
            "application-archimate-links": _empty_table(),
            "application-processes": _empty_table(),
        },
    }

    # --- Financial metrics ---
    def _f(attr):
        return float(getattr(app_obj, attr, None) or 0)

    cost_breakdown = {
        "license_annual": _f("license_cost_annual"),
        "infrastructure_monthly": _f("infrastructure_cost_monthly"),
        "infrastructure_annual": _f("infrastructure_cost_monthly") * 12,
        "development_annual": _f("development_cost_annual"),
        "maintenance_annual": _f("maintenance_cost"),
        "total_tco": _f("total_cost_of_ownership"),
        "calculated_total": (
            _f("license_cost_annual")
            + _f("infrastructure_cost_monthly") * 12
            + _f("development_cost_annual")
            + _f("maintenance_cost")
        ),
    }

    health_metrics = {
        "monitoring_enabled": getattr(app_obj, "monitoring_enabled", False),  # model-safety-ok
        "monitoring_tool": getattr(app_obj, "monitoring_tool", None),  # model-safety-ok
        "health_check_url": getattr(app_obj, "health_check_url", None),  # model-safety-ok
        "sla_availability": _f("sla_availability_percentage"),
        "current_uptime": _f("current_uptime_percentage"),
        "deployment_frequency": getattr(app_obj, "deployment_frequency", None),  # model-safety-ok
        "mttr_hours": getattr(app_obj, "mean_time_to_recovery_hours", None),  # model-safety-ok
        "change_failure_rate": _f("change_failure_rate_percent"),
    }

    quality_metrics = {
        "code_quality_score": _f("code_quality_score"),
        "technical_debt_hours": getattr(app_obj, "technical_debt_hours", 0) or 0,  # model-safety-ok
        "bugs_count": getattr(app_obj, "bugs_count", 0) or 0,  # model-safety-ok
        "vulnerabilities_count": getattr(app_obj, "vulnerabilities_count", 0) or 0,  # model-safety-ok
        "code_coverage": _f("code_coverage_percent"),
        "automated_testing_coverage": _f("automated_testing_coverage"),
        "last_scan_date": getattr(app_obj, "last_code_quality_scan", None),  # model-safety-ok
    }

    # --- Application documents ---
    application_documents = []
    try:
        from app.models.miscellaneous import ApplicationDocument
        # tenant-scoping-ok: FK-scoped to app_obj.id, an org-scoped
        # ApplicationComponent load.
        application_documents = (
            ApplicationDocument.query.filter_by(application_component_id=app_obj.id)
            .order_by(ApplicationDocument.uploaded_at.desc())
            .all()
        )
    except Exception:
        db.session.rollback()

    return render_template(
        "applications/dashboard.html",
        app=app_obj,
        # Not yet computed for this view. Pass None (never 0.0) so the template
        # renders an em dash instead of asserting a measured score of zero —
        # a literal 0.0 here rendered every application as red/non-compliant.
        functional_maturity=None,
        compliance_score=None,
        compliance_results=None,
        capabilities_with_compliance=[],
        edit_mode=request.args.get("edit") == "1",
        edit_mode_motivation=request.args.get("edit_motivation") == "1",
        edit_mode_strategy=request.args.get("edit_strategy") == "1",
        edit_mode_business=request.args.get("edit_business") == "1",
        edit_mode_application=request.args.get("edit_application") == "1",
        edit_mode_technology=request.args.get("edit_technology") == "1",
        edit_mode_physical=request.args.get("edit_physical") == "1",
        edit_mode_implementation=request.args.get("edit_implementation") == "1",
        edit_mode_health_quality=request.args.get("edit_health_quality") == "1",
        changed_field_ids=[],
        capabilities=capabilities,
        requirements=requirements,
        interfaces=interfaces,
        vendor_footprint=vendor_footprint,
        vendor_summary=vendor_summary,
        vendor_form_config=vendor_form_config,
        metric_cards=metric_cards,
        dashboard_config=dashboard_config,
        relationship_status_filters=[],
        capability_status_filters=[],
        archimate_link_status_filters=[],
        process_status_filters=[],
        capability_form_config=capability_form_config,
        archimate_form_config={},
        archimate_viewpoints=[],
        mermaid_diagrams=[],
        impact_analysis={},
        strategy_capabilities=[],
        strategy_resources=[],
        value_streams=[],
        courses_of_action=[],
        business_services=[],
        business_processes=[],
        business_actors=[],
        business_roles=[],
        business_collaborations=[],
        business_interfaces=[],
        business_functions=[],
        business_interactions=[],
        business_events=[],
        contracts=[],
        representations=[],
        application_services=[],
        application_interfaces_archimate=[],
        data_objects=[],
        application_components=[],
        application_functions=[],
        application_processes=[],
        application_interactions=[],
        application_events=[],
        application_collaborations=[],
        vendor_classifications_display=[],
        technology_nodes=[],
        technology_devices=[],
        system_software=[],
        technology_services=[],
        technology_interfaces=[],
        technology_collaborations=[],
        technology_functions=[],
        technology_processes=[],
        technology_interactions=[],
        technology_events=[],
        artifacts=[],
        physical_equipment=[],
        physical_facilities=[],
        physical_distribution_networks=[],
        physical_materials=[],
        work_packages=[],
        deliverables=[],
        plateaus=[],
        gaps=[],
        implementation_events=[],
        stakeholders=[],
        drivers=[],
        goals=[],
        outcomes=[],
        principles=[],
        requirements_archimate=[],
        constraints=[],
        assessments=[],
        values=[],
        meanings=[],
        dependencies_upstream=[],
        dependencies_downstream=[],
        dependency_graph_data={},
        cost_breakdown=cost_breakdown,
        health_metrics=health_metrics,
        quality_metrics=quality_metrics,
        integration_mappings=[],
        compliance_requirements=[],
        compliance_frameworks=[],
        technology_mappings=[],
        business_actor_mappings=[],
        application_documents=application_documents,
        arch_elements_by_layer={},
        currency_symbol=CurrencyConfig.get_currency_config()["symbol"],
        active_tab_id=request.args.get("tab", "overview"),
        linked_solutions=linked_solutions,
        archimate_info=archimate_info,
        archimate_relationships=archimate_relationships,
    )
