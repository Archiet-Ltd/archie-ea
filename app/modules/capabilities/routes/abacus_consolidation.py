"""
Migration: Copied from app/routes/abacus_consolidation.py -> app/modules/capabilities/routes/
Date: 2026-02-14 | Relative imports fixed for new location.

Abacus Capability Consolidation Routes

Admin UI for reviewing and merging duplicate capabilities after Abacus import.

Features:
- View side-by-side comparison of Abacus vs manual capabilities
- Fuzzy matching to suggest merge candidates
- Bulk merge capabilities with conflict resolution
- Hierarchy consolidation (merge parent-child trees)
- APQC mapping preservation during merge
- Audit trail of all merge operations
"""

import logging

from flask import Blueprint, jsonify, render_template, request
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)
from flask_login import login_required
# rapidfuzz (MIT), not fuzzywuzzy (GPL-2.0-only). fuzzywuzzy and its
# python-Levenshtein speedup are GPL-2.0, which cannot be sublicensed under
# Entelim's commercial licence — see docs/adr/0006. rapidfuzz is API-compatible
# for the functions used here; it returns a float where fuzzywuzzy returned
# int(round(...)), so call sites round to keep scores identical.
from rapidfuzz import fuzz
from sqlalchemy import or_

from app import db
from app.decorators import admin_required, audit_log
from app.models.apqc_process import CapabilityProcessMapping
from app.models.application_capability import ApplicationCapabilityMapping
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability

bp = Blueprint("abacus_consolidation", __name__, url_prefix="/admin/abacus/consolidation")


@bp.route("/")
@login_required
@admin_required
def index():
    """Dashboard for capability consolidation."""
    # Get statistics
    total_caps = BusinessCapability.query.count()
    abacus_caps = BusinessCapability.query.filter_by(discovery_source="abacus").count()
    manual_caps = BusinessCapability.query.filter(
        or_(
            BusinessCapability.discovery_source == "manual",
            BusinessCapability.discovery_source == None,
        )
    ).count()

    # Find potential duplicates
    duplicates = find_duplicate_candidates(limit=50)

    return render_template(
        "admin/abacus_consolidation.html",
        total_capabilities=total_caps,
        abacus_capabilities=abacus_caps,
        manual_capabilities=manual_caps,
        duplicate_candidates=duplicates,
    )


@bp.route("/candidates")
@login_required
@admin_required
def get_candidates():
    """Get duplicate merge candidates as JSON."""
    threshold = int(request.args.get("threshold", 80))
    limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

    candidates = find_duplicate_candidates(similarity_threshold=threshold, limit=limit)

    return jsonify({"candidates": candidates, "total": len(candidates)})


@bp.route("/compare/<int:abacus_id>/<int:manual_id>")
@login_required
@admin_required
def compare(abacus_id, manual_id):
    """Compare two capabilities side-by-side."""
    abacus_cap = BusinessCapability.query.get_or_404(abacus_id)
    manual_cap = BusinessCapability.query.get_or_404(manual_id)

    # Get child capabilities
    abacus_children = BusinessCapability.query.filter_by(parent_capability_id=abacus_id).all()
    manual_children = BusinessCapability.query.filter_by(parent_capability_id=manual_id).all()

    # Get APQC mappings
    abacus_apqc = CapabilityProcessMapping.query.filter_by(capability_id=abacus_id).all()
    manual_apqc = CapabilityProcessMapping.query.filter_by(capability_id=manual_id).all()

    # Get related applications (via capability mapping table)
    abacus_apps = (
        ApplicationComponent.query
        .join(ApplicationCapabilityMapping, ApplicationCapabilityMapping.application_component_id == ApplicationComponent.id)
        .filter(ApplicationCapabilityMapping.business_capability_id == abacus_id)
        .all()
    )
    manual_apps = (
        ApplicationComponent.query
        .join(ApplicationCapabilityMapping, ApplicationCapabilityMapping.application_component_id == ApplicationComponent.id)
        .filter(ApplicationCapabilityMapping.business_capability_id == manual_id)
        .all()
    )

    return render_template(
        "admin/abacus_compare.html",
        abacus_cap=abacus_cap,
        manual_cap=manual_cap,
        abacus_children=abacus_children,
        manual_children=manual_children,
        abacus_apqc=abacus_apqc,
        manual_apqc=manual_apqc,
        abacus_apps=abacus_apps,
        manual_apps=manual_apps,
    )


@bp.route("/merge", methods=["POST"])
@login_required
@admin_required
@audit_log("capabilities_merge")
def merge_capabilities():
    """Merge two capabilities into one."""
    data = request.get_json()

    source_id = data.get("source_id")
    target_id = data.get("target_id")
    merge_strategy = data.get("strategy", "keep_target")

    source_cap = BusinessCapability.query.get_or_404(source_id)
    target_cap = BusinessCapability.query.get_or_404(target_id)

    try:
        # Merge based on strategy
        if merge_strategy == "keep_target":
            # Keep target, migrate relationships from source
            result = merge_keep_target(source_cap, target_cap, data)
        elif merge_strategy == "keep_source":
            # Keep source, migrate relationships from target
            result = merge_keep_source(source_cap, target_cap, data)
        elif merge_strategy == "merge_fields":
            # Smart field merge
            result = merge_smart(source_cap, target_cap, data)
        else:
            return jsonify({"success": False, "error": "Invalid merge strategy"}), 400

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": f"Successfully merged {source_cap.name} into {target_cap.name}",
                "result": result,
            }
        )

    except Exception as e:
        db.session.rollback()
        logger.error("Error merging capabilities: %s", e, exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@bp.route("/bulk-merge", methods=["POST"])
@login_required
@admin_required
@audit_log("capabilities_bulk_merge")
def bulk_merge():
    """Merge multiple capability pairs at once."""
    data = request.get_json()
    merge_pairs = data.get("pairs", [])
    strategy = data.get("strategy", "keep_target")

    results = []
    errors = []

    for pair in merge_pairs:
        source_id = pair.get("source_id")
        target_id = pair.get("target_id")

        try:
            source_cap = BusinessCapability.query.get(source_id)
            target_cap = BusinessCapability.query.get(target_id)

            if not source_cap or not target_cap:
                errors.append(f"Capability not found: {source_id} or {target_id}")
                continue

            if strategy == "keep_target":
                result = merge_keep_target(source_cap, target_cap, pair)
            elif strategy == "keep_source":
                result = merge_keep_source(source_cap, target_cap, pair)
            else:
                result = merge_smart(source_cap, target_cap, pair)

            results.append(result)

        except Exception as e:
            logger.error("Failed to merge %s into %s: %s", source_id, target_id, e)
            errors.append(f"Failed to merge {source_id} into {target_id}")
            db.session.rollback()

    if not errors:
        db.session.commit()

    return jsonify(
        {"success": len(errors) == 0, "merged": len(results), "errors": errors, "results": results}
    )


@bp.route("/reject/<int:abacus_id>/<int:manual_id>", methods=["POST"])
@login_required
@admin_required
def reject_merge(abacus_id, manual_id):
    """Mark a suggested merge as rejected (not a duplicate)."""
    # You could store rejected pairs in a table to avoid suggesting again
    # For now, just return success
    return jsonify({"success": True, "message": "Merge suggestion rejected"})


# Helper functions


def find_duplicate_candidates(similarity_threshold=80, limit=100):
    """Find potential duplicate capabilities using fuzzy matching."""
    abacus_caps = BusinessCapability.query.filter_by(discovery_source="abacus").all()

    manual_caps = BusinessCapability.query.filter(
        or_(
            BusinessCapability.discovery_source == "manual",
            BusinessCapability.discovery_source == None,
        )
    ).all()

    candidates = []

    for abacus_cap in abacus_caps:
        for manual_cap in manual_caps:
            # Calculate similarity
            name_similarity = round(fuzz.token_sort_ratio(
                abacus_cap.name.lower(), manual_cap.name.lower()
            ))

            if name_similarity >= similarity_threshold:
                # Check if codes match
                code_match = (
                    abacus_cap.code
                    and manual_cap.code
                    and abacus_cap.code.replace("ABACUS-", "")
                    == manual_cap.code.replace("MANUAL-", "")
                )

                candidates.append(
                    {
                        "abacus_id": abacus_cap.id,
                        "abacus_name": abacus_cap.name,
                        "abacus_code": abacus_cap.code,
                        "abacus_description": abacus_cap.description,
                        "manual_id": manual_cap.id,
                        "manual_name": manual_cap.name,
                        "manual_code": manual_cap.code,
                        "manual_description": manual_cap.description,
                        "similarity": name_similarity,
                        "code_match": code_match,
                        "recommendation": "AUTO_MERGE" if name_similarity > 95 else "REVIEW",
                    }
                )

    # Sort by similarity descending
    candidates.sort(key=lambda x: x["similarity"], reverse=True)

    return candidates[:limit]


def merge_keep_target(source, target, options):
    """Merge source into target, keeping target's data."""
    # Migrate child capabilities
    children = BusinessCapability.query.filter_by(parent_capability_id=source.id).all()
    for child in children:
        child.parent_capability_id = target.id

    # Migrate APQC mappings (avoid duplicates)
    source_mappings = CapabilityProcessMapping.query.filter_by(capability_id=source.id).all()

    existing_apqc_ids = {
        m.apqc_process_id
        for m in CapabilityProcessMapping.query.filter_by(capability_id=target.id).all()
    }

    for mapping in source_mappings:
        if mapping.apqc_process_id not in existing_apqc_ids:
            mapping.capability_id = target.id
        else:
            # Duplicate mapping, delete
            db.session.delete(mapping)

    # Migrate application-capability mappings
    # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
    app_mappings = ApplicationCapabilityMapping.query.filter_by(business_capability_id=source.id).all()
    existing_app_ids = {
        m.application_component_id
        # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
        for m in ApplicationCapabilityMapping.query.filter_by(business_capability_id=target.id).all()
    }
    for mapping in app_mappings:
        if mapping.application_component_id not in existing_app_ids:
            mapping.business_capability_id = target.id
        else:
            db.session.delete(mapping)

    # Store alias for search
    if not target.abacus_name and source.name:
        target.abacus_name = source.name

    # Delete source
    db.session.delete(source)

    return {
        "kept": target.id,
        "deleted": source.id,
        "children_migrated": len(children),
        "apqc_migrated": len(source_mappings),
        "apps_migrated": len(app_mappings),
    }


def merge_keep_source(source, target, options):
    """Merge target into source, keeping source's data."""
    return merge_keep_target(target, source, options)


def merge_smart(source, target, options):
    """Smart merge with field-level conflict resolution."""
    field_rules = options.get("field_rules", {})

    # Apply field rules
    for field, rule in field_rules.items():
        if rule == "keep_source":
            setattr(target, field, getattr(source, field))
        elif rule == "keep_target":
            pass  # Already has target value
        elif rule == "concat":
            target_val = getattr(target, field) or ""
            source_val = getattr(source, field) or ""
            if target_val and source_val and target_val != source_val:
                setattr(target, field, f"{target_val}\n\n[From Abacus]: {source_val}")

    # Then do standard relationship migration
    return merge_keep_target(source, target, options)


@bp.route("/orphans")
@login_required
@admin_required
def orphan_report():
    """Report on capabilities with missing parent links (orphaned hierarchy)."""
    # Find L2/L3 capabilities with no parent_capability_id
    orphans = (
        BusinessCapability.query
        .filter(
            BusinessCapability.level > 1,
            BusinessCapability.parent_capability_id.is_(None),
        )
        .order_by(BusinessCapability.level, BusinessCapability.name)
        .all()
    )

    orphan_data = []
    for cap in orphans:
        orphan_data.append({
            "id": cap.id,
            "name": cap.name,
            "level": cap.level,
            "archimate_id": cap.archimate_id,  # model-safety-ok: direct field access
            "discovery_source": cap.discovery_source,
            "business_domain": cap.business_domain,
        })

    return jsonify({
        "orphan_count": len(orphan_data),
        "orphans": orphan_data,
    })
