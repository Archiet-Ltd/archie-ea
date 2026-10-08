"""
DEPRECATED: This file is migrated to app/modules/governance/.
Registration is now centralized via app.modules.governance.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Consolidation List Routes

Routes for managing the consolidation list - applications marked for consolidation
that can be planned for decommissioning, retirement, or added to roadmap.
"""

from decimal import Decimal

from werkzeug.exceptions import HTTPException
import logging
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy import or_

from app.decorators import audit_log
from app.services.application_cost_accessor import set_annual_cost

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.consolidation_list import (
    ConsolidationAction,
    ConsolidationListEntry,
    CONSOLIDATION_STATUS_MAP,
)
from app.utils.pagination import safe_int_arg
from app.utils.tenant_sql import current_org_id

logger = logging.getLogger(__name__)

# Create blueprint
consolidation_list_bp = Blueprint("consolidation_list", __name__, url_prefix="/consolidation-list")


def _entry_in_caller_org(entry_id):
    """Fetch a ConsolidationListEntry only if its application belongs to the
    caller's organisation. ConsolidationListEntry carries no organization_id
    of its own -- ownership is via application_id -- so a bare
    ConsolidationListEntry.query.get(...) would return any organisation's
    entry. Fail closed: no ambient org, no application, or a foreign
    application all refuse rather than guess.
    """
    org_id = current_org_id()
    if org_id is None:
        return None
    entry = ConsolidationListEntry.query.get(entry_id)
    if entry is None or not entry.application_id:
        return None
    owned_app = db.session.execute(
        db.select(ApplicationComponent).where(
            ApplicationComponent.id == entry.application_id,
            ApplicationComponent.organization_id == org_id,
        )
    ).scalar_one_or_none()
    return entry if owned_app is not None else None


@consolidation_list_bp.route("/")
@login_required
def dashboard():
    """Consolidation list dashboard"""
    try:
        from flask import url_for

        try:
            dashboard_registry_url = url_for("dynamic_dashboards.model_registry_index")
        except HTTPException:
            raise
        except Exception as e:
            logger.debug("Could not resolve dashboard registry URL: %s", e)
            dashboard_registry_url = "/auto-dashboard/registry"
        return render_template(
            "consolidation_list/dashboard.html", dashboard_registry_url=dashboard_registry_url
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading consolidation dashboard: {e}")
        return f"Error loading dashboard: {str(e)}", 500


@consolidation_list_bp.route("/api/entries")
@login_required
def get_entries():
    """Get consolidation list entries with filtering and pagination."""
    try:
        status_filter = (request.args.get("status") or "").strip()
        action_filter = (request.args.get("action") or "").strip()
        search_query = (request.args.get("search") or "").strip()
        wave_filter = request.args.get("wave", type=int)
        missing_filter = (request.args.get("missing") or "").strip()
        page = max(safe_int_arg('page', 1, minimum=1) or 1, 1)
        per_page = safe_int_arg('per_page', 25, minimum=1, maximum=500) or 25
        per_page = max(10, min(per_page, 100))

        filtered_query = ConsolidationListEntry.query.join(
            ApplicationComponent,
            ConsolidationListEntry.application_id == ApplicationComponent.id,
            isouter=True,
        )

        # Map legacy status values for filtering
        if status_filter:
            mapped = CONSOLIDATION_STATUS_MAP.get(status_filter, status_filter)
            filtered_query = filtered_query.filter(
                ConsolidationListEntry.status == mapped
            )
        if action_filter:
            filtered_query = filtered_query.filter(
                ConsolidationListEntry.recommended_action == action_filter
            )
        if wave_filter is not None:
            filtered_query = filtered_query.filter(
                ConsolidationListEntry.wave == wave_filter
            )
        if missing_filter == "owner":
            filtered_query = filtered_query.filter(
                or_(
                    ConsolidationListEntry.assigned_to.is_(None),
                    ConsolidationListEntry.assigned_to == "",
                )
            )
        elif missing_filter == "target_date":
            filtered_query = filtered_query.filter(
                ConsolidationListEntry.target_date.is_(None)
            )
        elif missing_filter == "roadmap":
            filtered_query = filtered_query.filter(
                ConsolidationListEntry.roadmap_item_id.is_(None)
            )
        if search_query:
            search_like = f"%{search_query}%"
            filtered_query = filtered_query.filter(
                or_(
                    ApplicationComponent.name.ilike(search_like),
                    ConsolidationListEntry.source_group_name.ilike(search_like),
                    ConsolidationListEntry.recommended_action.ilike(search_like),
                    ApplicationComponent.application_owner.ilike(search_like),
                )
            )

        ordered_query = filtered_query.order_by(
            ConsolidationListEntry.priority.desc(), ConsolidationListEntry.added_at.desc()
        )
        pagination = ordered_query.paginate(page=page, per_page=per_page, error_out=False)
        entries = pagination.items

        entries_data = [entry.to_dict() for entry in entries]

        # Enrich entries with TIME scores from ApplicationRationalizationScore
        try:
            from app.models.application_rationalization import ApplicationRationalizationScore
            entry_app_ids = [e.application_id for e in entries if e.application_id]
            if entry_app_ids:
                time_scores = ApplicationRationalizationScore.query.filter(
                    ApplicationRationalizationScore.application_component_id.in_(entry_app_ids)
                ).all()
                time_score_map = {s.application_component_id: s for s in time_scores}
                time_action_map = {
                    "ELIMINATE": "decommission",
                    "MIGRATE": "replace",
                    "INVEST": "modernize",
                    "TOLERATE": "pending_review",
                }
                for ed in entries_data:
                    score = time_score_map.get(ed.get("application_id"))
                    if score:
                        ed["time_action"] = score.rationalization_action
                        ed["time_score"] = score.overall_health_score
                        # If the entry still has pending_review, override with TIME action
                        if ed.get("recommended_action") == "pending_review" and score.rationalization_action:
                            mapped = time_action_map.get(score.rationalization_action.upper())
                            if mapped:
                                ed["recommended_action"] = mapped
                                # Also update the DB entry
                                entry_obj = ConsolidationListEntry.query.get(ed["id"])
                                if entry_obj and entry_obj.recommended_action == "pending_review":
                                    entry_obj.recommended_action = mapped
                    else:
                        ed["time_action"] = None
                        ed["time_score"] = None
                db.session.commit()
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not enrich entries with TIME scores: {e}")

        summary_entries = filtered_query.all()

        # Calculate summary statistics
        total_entries = len(summary_entries)
        total_savings = sum(float(e.estimated_savings or 0) for e in summary_entries)
        by_status = {}
        by_action = {}

        for entry in summary_entries:
            mapped_status = CONSOLIDATION_STATUS_MAP.get(entry.status, entry.status) or "identified"
            by_status[mapped_status] = by_status.get(mapped_status, 0) + 1
            action = entry.recommended_action if entry.recommended_action else "pending"
            by_action[action] = by_action.get(action, 0) + 1

        # Enriched summary stats
        summary = {
            "total_entries": total_entries,
            "total_estimated_savings": total_savings,
            "by_status": by_status,
            "by_action": by_action,
            "portfolio_annual_cost": sum(
                float(getattr(e.application, "license_cost", 0) or 0)
                + float(getattr(e.application, "support_cost", 0) or 0)
                + float(getattr(e.application, "infrastructure_cost", 0) or 0)
                for e in summary_entries if e.application
            ),
            "savings_verified_total": sum(
                float(e.estimated_savings or 0)
                for e in summary_entries if e.savings_verified
            ),
            "savings_estimated_total": sum(
                float(e.estimated_savings or 0)
                for e in summary_entries if not e.savings_verified
            ),
            "total_migration_cost": sum(
                float(e.migration_cost or 0) for e in summary_entries
            ),
            "active_count": sum(
                1 for e in summary_entries
                if (CONSOLIDATION_STATUS_MAP.get(e.status, e.status) or "identified")
                in ("approved", "migration_planned", "in_progress")
            ),
            "completed_count": sum(
                1 for e in summary_entries
                if (CONSOLIDATION_STATUS_MAP.get(e.status, e.status) or "identified") == "completed"
            ),
            "attention_count": sum(
                1 for e in summary_entries if not e.assigned_to or not e.roadmap_item_id
            ),
            "high_risk_count": sum(
                1 for e in summary_entries if e.migration_complexity == "high"
            ),
            "no_roadmap_count": sum(1 for e in summary_entries if not e.roadmap_item_id),
            "no_owner_count": sum(1 for e in summary_entries if not e.assigned_to),
            "no_target_date_count": sum(1 for e in summary_entries if not e.target_date),
        }

        # Enrich summary with rationalization workbench data.
        # When ConsolidationListEntry is empty the summary cards show all zeros.
        # Pull identified candidates from ApplicationRationalizationScore so the
        # dashboard reflects the actual portfolio state even before entries are added.
        try:
            from app.models.application_rationalization import ApplicationRationalizationScore
            rat_candidates = ApplicationRationalizationScore.query.filter(
                ApplicationRationalizationScore.rationalization_action.in_(
                    ["ELIMINATE", "MIGRATE"]
                )
            ).all()
            rat_retire = sum(
                1 for r in rat_candidates if r.rationalization_action == "ELIMINATE"
            )
            rat_consolidate = sum(
                1 for r in rat_candidates
                if r.rationalization_action == "MIGRATE"
                or (r.disposition_action or "").lower() == "consolidate"
            )
            rat_tco_total = sum(
                float(r.total_cost_of_ownership or 0) for r in rat_candidates
            )
            # Estimated savings: apply a conservative 60% of TCO for ELIMINATE actions
            rat_savings_estimate = sum(
                float(r.total_cost_of_ownership or 0) * 0.6
                for r in rat_candidates if r.rationalization_action == "ELIMINATE"
            )
            summary["rationalization_candidates"] = len(rat_candidates)
            summary["rationalization_retire_count"] = rat_retire
            summary["rationalization_consolidate_count"] = rat_consolidate
            summary["rationalization_tco_total"] = rat_tco_total
            summary["rationalization_savings_estimate"] = rat_savings_estimate
            # If the ConsolidationListEntry table is empty, surface the rationalization totals
            # as the primary portfolio_annual_cost and total_estimated_savings so cards are non-zero
            if total_entries == 0:
                summary["portfolio_annual_cost"] = rat_tco_total
                summary["total_estimated_savings"] = rat_savings_estimate
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Could not enrich summary with rationalization data: %s", e)

        return jsonify(
            {
                "success": True,
                "entries": entries_data,
                "summary": summary,
                "pagination": {
                    "page": pagination.page,
                    "per_page": pagination.per_page,
                    "total": pagination.total,
                    "pages": pagination.pages,
                    "has_prev": pagination.has_prev,
                    "has_next": pagination.has_next,
                },
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting consolidation entries: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/add", methods=["POST"])
@login_required
@audit_log("consolidation_add")
def add_to_list():
    """Add applications to consolidation list"""
    try:
        data = request.get_json()
        if not data or "application_ids" not in data:
            return jsonify({"success": False, "error": "Missing application_ids in request"}), 400

        application_ids = data["application_ids"]
        if not isinstance(application_ids, list) or len(application_ids) == 0:
            return (
                jsonify({"success": False, "error": "application_ids must be a non-empty array"}),
                400,
            )

        source_group_id = data.get("source_group_id")
        source_group_name = data.get("source_group_name", "Manual Selection")
        source_type = data.get("source_type", "duplicate_detection")

        added_count = 0
        skipped_count = 0
        errors = []

        for app_id in application_ids:
            try:
                app_id_int = int(str(app_id))

                # Check if already in list
                existing = ConsolidationListEntry.query.filter_by(
                    application_id=app_id_int, status="pending"
                ).first()

                if existing:
                    skipped_count += 1
                    continue

                # Get application to get name and cost info
                app = ApplicationComponent.query.get(app_id_int)
                if not app:
                    errors.append(f"Application {app_id_int} not found")
                    continue

                # Calculate estimated savings if available
                estimated_savings = None
                if hasattr(app, "total_cost_of_ownership") and app.total_cost_of_ownership:
                    estimated_savings = app.total_cost_of_ownership
                elif hasattr(app, "maintenance_cost_annual") and app.maintenance_cost_annual:
                    estimated_savings = app.maintenance_cost_annual

                # Create entry
                entry = ConsolidationListEntry(
                    application_id=app_id_int,
                    source_group_id=source_group_id,
                    source_group_name=source_group_name,
                    source_type=source_type,
                    recommended_action="pending_review",  # Use string value
                    estimated_savings=estimated_savings,
                    added_by=request.remote_addr,  # Could use current_user.email if available
                    added_at=datetime.utcnow(),
                )

                db.session.add(entry)
                added_count += 1

            except (ValueError, TypeError) as e:
                errors.append(f"Invalid application ID {app_id}: {str(e)}")
            except HTTPException:
                raise
            except Exception as e:
                errors.append(f"Error adding application {app_id}: {str(e)}")

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "added_count": added_count,
                "skipped_count": skipped_count,
                "errors": errors,
                "message": f"Added {added_count} application(s) to consolidation list",
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding to consolidation list: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/entry/<int:entry_id>/detail")
@login_required
def get_entry_detail(entry_id):
    """Get enriched detail for a consolidation entry (lazy load on row expand)."""
    try:
        entry = _entry_in_caller_org(entry_id)
        if entry is None:
            return jsonify({"success": False, "error": "Entry not found"}), 404
        app = entry.application

        # Financial
        license_cost = float(getattr(app, "license_cost", 0) or 0) if app else 0
        support_cost = float(getattr(app, "support_cost", 0) or 0) if app else 0
        infrastructure_cost = float(getattr(app, "infrastructure_cost", 0) or 0) if app else 0
        maintenance_cost = float(getattr(app, "maintenance_cost", 0) or 0) if app else 0
        total_annual = license_cost + support_cost + infrastructure_cost + maintenance_cost
        migration = float(entry.migration_cost or 0)
        est_savings = float(entry.estimated_savings or 0)
        payback = (migration / est_savings * 12) if est_savings > 0 else None

        # Capabilities
        capabilities = []
        try:
            from app.models.capability_to_vendor_mapping import (
                UnifiedCapabilityApplicationMapping,
            )
            from app.models.unified_capability import UnifiedCapability

            mappings = UnifiedCapabilityApplicationMapping.query.filter_by(
                application_component_id=entry.application_id
            ).all()
            for m in mappings:
                cap = db.session.get(UnifiedCapability, m.unified_capability_id)
                if cap:
                    capabilities.append({
                        "id": cap.id,
                        "name": cap.name,
                        "level": getattr(cap, "level", None),
                        "is_critical": (
                            getattr(cap, "is_critical", False)
                            or (getattr(app, "business_criticality", "") or "").lower() == "critical"
                        ),
                    })
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not load capabilities for entry {entry_id}: {e}")

        # Links (verified routes)
        links = {
            "app_detail": f"/applications/{entry.application_id}" if app else None,
            "roadmap_builder": "/api/roadmap-builder/summary",
            "architecture_assistant": "/architecture-assistant/",
        }

        return jsonify({
            "success": True,
            "financial": {
                "license_cost": license_cost,
                "support_cost": support_cost,
                "infrastructure_cost": infrastructure_cost,
                "maintenance_cost": maintenance_cost,
                "total_annual_cost": total_annual,
                "migration_cost": migration,
                "estimated_savings": est_savings,
                "savings_verified": entry.savings_verified or False,
                "net_annual_saving": est_savings,
                "payback_months": round(payback, 1) if payback else None,
            },
            "capabilities": capabilities,
            "risk": {
                "migration_complexity": entry.migration_complexity,
                "data_disposition": entry.data_disposition,
                "regulatory_flags": entry.regulatory_flags or [],
                "risk_assessment": entry.risk_assessment,
            },
            "target_application": {
                "id": entry.target_application_id,
                "name": entry.target_application.name if entry.target_application else None,
            } if entry.target_application_id else None,
            "links": links,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting entry detail {entry_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/entry/<int:entry_id>", methods=["PUT"])
@login_required
@audit_log("consolidation_update")
def update_entry(entry_id):
    """Update a consolidation list entry"""
    try:
        entry = _entry_in_caller_org(entry_id)
        if entry is None:
            return jsonify({"success": False, "error": "Entry not found"}), 404
        data = request.get_json()

        # Update fields
        if "recommended_action" in data:
            valid_actions = [action.value for action in ConsolidationAction]
            action_value = data["recommended_action"]
            if action_value not in valid_actions:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": f'Invalid action: {action_value}. Must be one of: {", ".join(valid_actions)}',
                        }
                    ),
                    400,
                )
            entry.recommended_action = action_value

        if "priority" in data:
            entry.priority = data["priority"]

        if "status" in data:
            from flask_login import current_user

            entry.status = data["status"]
            entry.status_changed_at = datetime.utcnow()
            entry.status_changed_by = (
                getattr(current_user, "email", None) or str(current_user.id)
            )

        if "target_date" in data:
            if data["target_date"]:
                entry.target_date = datetime.fromisoformat(
                    data["target_date"].replace("Z", "+00:00")
                ).date()
            else:
                entry.target_date = None

        if "notes" in data:
            entry.notes = data["notes"]

        if "assigned_to" in data:
            entry.assigned_to = data["assigned_to"]

        if "business_rationale" in data:
            entry.business_rationale = data["business_rationale"]

        if "risk_assessment" in data:
            entry.risk_assessment = data["risk_assessment"]

        # Handle estimated_savings explicitly
        if "estimated_savings" in data:
            entry.estimated_savings = data["estimated_savings"]

        # New enrichment fields
        for field in (
            "wave", "migration_cost", "migration_complexity",
            "data_disposition", "savings_verified", "target_application_id",
            "consolidation_complexity",
        ):
            if field in data:
                setattr(entry, field, data[field])
        if "regulatory_flags" in data:
            entry.regulatory_flags = data["regulatory_flags"]

        # Back-propagate annual operating cost to Application model if provided
        if "annual_operating_cost" in data and data["annual_operating_cost"]:
            try:
                app = ApplicationComponent.query.get(entry.application_id)
                if app:
                    set_annual_cost(app, Decimal(str(data["annual_operating_cost"])))
                    app.updated_at = datetime.utcnow()
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Could not back-propagate TCO for entry {entry_id}: {e}")

        # RATA-014: Auto-create roadmap item when target_quarter is set
        if "target_quarter" in data and data["target_quarter"]:
            entry.target_quarter = data["target_quarter"]
            if not entry.roadmap_item_id:
                try:
                    # Create a lightweight roadmap reference
                    # (roadmap_item_id serves as a flag that a roadmap entry exists)
                    entry.roadmap_item_id = entry.id  # Self-reference as roadmap marker
                    logger.info(
                        "RATA-014: Auto-created roadmap link for consolidation %d, quarter %s",
                        entry.id, data["target_quarter"]
                    )
                except HTTPException:
                    raise
                except Exception as roadmap_err:
                    logger.error("RATA-014 roadmap auto-create failed: %s", roadmap_err)

        # RATA-015: Flag for benefits tracking when status changes to completed
        benefits_prompt = False
        if "status" in data and data["status"] == "completed":
            benefits_prompt = True

        entry.updated_at = datetime.utcnow()
        db.session.commit()

        result = {"success": True, "entry": entry.to_dict()}
        if benefits_prompt:
            result["benefits_prompt"] = True
            result["estimated_savings"] = float(entry.estimated_savings or 0)
        return jsonify(result)

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating consolidation entry {entry_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/entry/<int:entry_id>", methods=["DELETE"])
@login_required
@audit_log("consolidation_remove")
def remove_entry(entry_id):
    """Remove an entry from consolidation list"""
    try:
        entry = _entry_in_caller_org(entry_id)
        if entry is None:
            return jsonify({"success": False, "error": "Entry not found"}), 404
        db.session.delete(entry)
        db.session.commit()

        return jsonify({"success": True, "message": "Entry removed from consolidation list"})

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing consolidation entry {entry_id}: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/bulk-action", methods=["POST"])
@login_required
@audit_log("consolidation_bulk_action")
def bulk_action():
    """Perform bulk actions on consolidation list entries"""
    try:
        data = request.get_json()
        if not data or "entry_ids" not in data or "action" not in data:
            return (
                jsonify({"success": False, "error": "Missing entry_ids or action in request"}),
                400,
            )

        entry_ids = data["entry_ids"]
        action = data["action"]

        # Validate action
        valid_actions = [
            "decommission", "retire", "add_to_roadmap", "update_status",
            "remove", "set_wave", "set_priority", "assign_owner",
        ]
        if action not in valid_actions:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f'Invalid action. Must be one of: {", ".join(valid_actions)}',
                    }
                ),
                400,
            )

        updated_count = 0
        errors = []

        for entry_id in entry_ids:
            try:
                entry = _entry_in_caller_org(entry_id)
                if not entry:
                    errors.append(f"Entry {entry_id} not found")
                    continue

                if action == "decommission":
                    entry.recommended_action = "decommission"
                    entry.status = "approved"
                elif action == "retire":
                    entry.recommended_action = "retire"
                    entry.status = "approved"
                elif action == "add_to_roadmap":
                    entry.recommended_action = "add_to_roadmap"
                    entry.status = "approved"
                elif action == "update_status":
                    new_status = data.get("status", "approved")
                    entry.status = new_status
                elif action == "remove":
                    db.session.delete(entry)
                elif action == "set_wave":
                    wave_val = data.get("wave")
                    if wave_val is not None:
                        entry.wave = int(wave_val)
                elif action == "set_priority":
                    priority_val = data.get("priority", "medium")
                    entry.priority = priority_val
                elif action == "assign_owner":
                    owner_val = data.get("owner", "")
                    entry.assigned_to = owner_val

                entry.updated_at = datetime.utcnow()
                updated_count += 1

            except HTTPException:

                raise

            except Exception as e:
                errors.append(f"Error processing entry {entry_id}: {str(e)}")

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "updated_count": updated_count,
                "errors": errors,
                "message": f"Processed {updated_count} entry/entries",
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in bulk action: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/create-roadmap-task", methods=["POST"])
@login_required
@audit_log("consolidation_roadmap_task")
def create_roadmap_task():
    """Create a roadmap task for a consolidation list entry"""
    try:
        data = request.get_json()
        if not data or "entry_id" not in data:
            return jsonify({"success": False, "error": "Missing entry_id"}), 400

        entry_id = data.get("entry_id")
        start_date_str = data.get("start_date")
        end_date_str = data.get("end_date")
        owner = data.get("owner")

        # Get the consolidation entry
        entry = _entry_in_caller_org(entry_id)
        if not entry:
            return jsonify({"success": False, "error": "Consolidation entry not found"}), 404

        # Get the application
        app = ApplicationComponent.query.get(entry.application_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        # Try to create a RoadmapTask
        try:
            from ..models.roadmap import RoadmapTask
            from datetime import datetime as dt

            start_date = dt.fromisoformat(start_date_str.replace("Z", "+00:00")).date()
            end_date = dt.fromisoformat(end_date_str.replace("Z", "+00:00")).date()

            task_title = f"Consolidate: {app.name}"
            task_description = data.get("business_justification", f"Consolidation of {app.name}")

            roadmap_task = RoadmapTask(
                title=task_title,
                description=task_description,
                start_date=start_date,
                end_date=end_date,
                status="planned",
                assigned_to=owner,
                priority=entry.priority or "medium",
                estimated_hours=float(data.get("estimated_hours", 0)) or 0,
                percent_complete=0,
                archimate_element_type="ImplementationEvent",
                capability_level="L3",
            )

            db.session.add(roadmap_task)
            db.session.flush()  # Get the task ID

            # Update consolidation entry to link to roadmap
            entry.recommended_action = "add_to_roadmap"
            entry.status = "approved"
            entry.target_date = end_date
            entry.assigned_to = owner
            entry.roadmap_item_id = roadmap_task.id
            entry.business_rationale = data.get("business_justification", "")
            entry.risk_assessment = data.get("dependencies", "")
            entry.updated_at = datetime.utcnow()

            db.session.commit()

            logger.info(
                f"Created roadmap task {roadmap_task.id} for consolidation entry {entry_id}: {task_title}"
            )

            return jsonify(
                {
                    "success": True,
                    "message": f"Added {app.name} to roadmap",
                    "roadmap_task_id": roadmap_task.id,
                }
            )

        except ImportError:
            # error-signalling-ok: this branch still commits the entry, so success is true and the message discloses that no roadmap task was created
            #
            # Note it is currently UNREACHABLE:
            # app.modules.governance.models.roadmap.RoadmapTask exists and
            # imports cleanly, so the ImportError never fires. Kept rather than
            # deleted because the branch is honest if it ever does - unlike the
            # three guarded imports found elsewhere in this audit, which named
            # modules that have never existed and silently disabled a feature
            # apiece.
            entry.recommended_action = "add_to_roadmap"
            entry.status = "approved"
            entry.target_date = end_date_str
            entry.assigned_to = owner
            entry.updated_at = datetime.utcnow()
            db.session.commit()

            # error-signalling-ok: the entry above is committed, so success is true; the message discloses that no roadmap task was created
            return jsonify(
                {
                    "success": True,
                    "message": f"Added {app.name} to consolidation roadmap (roadmap task model not available)",
                }
            )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating roadmap task: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/recalculate-savings", methods=["POST"])
@login_required
@audit_log("consolidation_recalculate_savings")
def recalculate_savings():
    """Recalculate estimated savings for all consolidation entries using documented formulas."""
    try:
        entries = ConsolidationListEntry.query.all()
        updated_count = 0
        real_data_count = 0
        baseline_count = 0
        entry_details = []

        for entry in entries:
            app = ApplicationComponent.query.get(entry.application_id)
            if not app:
                continue

            # Calculate estimated savings using documented formulas:
            # - Infrastructure savings: 30% of hosting/infrastructure costs
            # - Support savings: 40% of support costs
            # - For apps with zero cost data: £10,000 baseline per redundant app
            estimated_savings = 0
            cost_source = "baseline"
            cost_breakdown = {}

            tco = float(app.total_cost_of_ownership or 0)
            infra = float(app.infrastructure_cost or 0)
            support = float(app.support_cost or 0)
            maint = float(app.maintenance_cost or 0)
            license_c = float(app.license_cost or 0)

            if tco > 0:
                # If TCO is available, use it directly
                estimated_savings = tco
                cost_source = "total_cost_of_ownership"
                cost_breakdown["tco"] = tco
            elif any([infra, support, maint, license_c]):
                # Use documented formulas on available partial data
                infra_savings = infra * 0.30  # 30% of infrastructure
                support_savings = support * 0.40  # 40% of support
                maint_savings = maint  # 100% of maintenance if decommissioned
                license_savings = license_c  # 100% of license if decommissioned
                estimated_savings = infra_savings + support_savings + maint_savings + license_savings
                cost_source = "partial_cost_data"
                cost_breakdown = {
                    "infrastructure_savings_30pct": infra_savings,
                    "support_savings_40pct": support_savings,
                    "maintenance_savings": maint_savings,
                    "license_savings": license_savings,
                }
            else:
                # No cost data at all: savings cannot be estimated. Record it as
                # unknown (None -> em dash) rather than inventing a plausible
                # figure — a fabricated £10,000 is indistinguishable from a real
                # estimate once persisted and shown.
                estimated_savings = None
                cost_source = "unknown"

            if estimated_savings is None:
                if entry.estimated_savings is not None:
                    entry.estimated_savings = None
                    entry.updated_at = datetime.utcnow()
                    updated_count += 1
            elif estimated_savings != float(entry.estimated_savings or 0):
                entry.estimated_savings = estimated_savings
                entry.updated_at = datetime.utcnow()
                updated_count += 1

            if cost_source == "unknown":
                baseline_count += 1
            else:
                real_data_count += 1

            entry_details.append({
                "entry_id": entry.id,
                "app_name": app.name,
                "savings": estimated_savings,
                "source": cost_source,
                "breakdown": cost_breakdown,
            })

        db.session.commit()

        logger.info(f"Recalculated savings for {updated_count} consolidation entries")

        return jsonify(
            {
                "success": True,
                "message": f"Recalculated savings for {updated_count} entries",
                "updated_count": updated_count,
                "real_data_count": real_data_count,
                "baseline_count": baseline_count,
                "details": entry_details,
            }
        )

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error recalculating savings: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/bulk-cost-import", methods=["POST"])
@login_required
@audit_log("consolidation_bulk_cost_import")
def bulk_cost_import():
    """Import cost data from CSV. Accepts multipart/form-data with a CSV file.

    CSV columns: application_name (or application_id), annual_operating_cost,
    license_cost, infrastructure_cost, support_cost, maintenance_cost
    """
    import csv
    import io

    try:
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file uploaded"}), 400

        file = request.files["file"]
        if not file.filename or not file.filename.endswith(".csv"):
            return jsonify({"success": False, "error": "File must be a .csv"}), 400

        content = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        updated_count = 0
        skipped_count = 0
        errors = []

        for row_num, row in enumerate(reader, start=2):
            try:
                # Find application by ID or name
                app = None
                app_id = row.get("application_id", "").strip()
                app_name = row.get("application_name", "").strip()

                if app_id:
                    app = ApplicationComponent.query.get(int(app_id))
                elif app_name:
                    app = ApplicationComponent.query.filter(
                        db.func.lower(ApplicationComponent.name) == app_name.lower()
                    ).first()

                if not app:
                    errors.append(f"Row {row_num}: Application not found (id={app_id}, name={app_name})")
                    skipped_count += 1
                    continue

                # Update cost fields
                changed = False
                cost_fields = {
                    "annual_operating_cost": "total_cost_of_ownership",
                    "license_cost": "license_cost",
                    "infrastructure_cost": "infrastructure_cost",
                    "support_cost": "support_cost",
                    "maintenance_cost": "maintenance_cost",
                }
                for csv_field, model_field in cost_fields.items():
                    val = row.get(csv_field, "").strip()
                    if val:
                        try:
                            numeric_val = float(val.replace(",", "").replace("£", "").replace("$", ""))
                            if model_field == "total_cost_of_ownership":
                                set_annual_cost(app, Decimal(str(numeric_val)))
                            else:
                                setattr(app, model_field, numeric_val)
                            changed = True
                        except ValueError:
                            errors.append(f"Row {row_num}: Invalid number in {csv_field}: {val}")

                if changed:
                    app.updated_at = datetime.utcnow()
                    updated_count += 1

            except HTTPException:

                raise

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                skipped_count += 1

        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"Updated costs for {updated_count} applications",
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "errors": errors[:50] if errors else [],
        })

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in bulk cost import: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@consolidation_list_bp.route("/api/score-all", methods=["POST"])
@login_required
@audit_log("consolidation_score_all")
def score_all_entries():
    """Auto-score all consolidation entries using available application metadata.

    Uses the TIME framework scoring logic to calculate scores from
    application attributes without requiring the manual questionnaire.
    """
    try:
        from app.models.application_rationalization import ApplicationRationalizationScore

        entries = ConsolidationListEntry.query.all()
        scored_count = 0
        skipped_count = 0
        time_action_map = {
            "ELIMINATE": "decommission",
            "MIGRATE": "replace",
            "INVEST": "modernize",
            "TOLERATE": "pending_review",
        }

        for entry in entries:
            app = ApplicationComponent.query.get(entry.application_id)
            if not app:
                continue

            # Check if score already exists
            existing = ApplicationRationalizationScore.query.filter_by(
                application_component_id=app.id
            ).first()
            if existing:
                # Update consolidation entry action from existing score
                if existing.rationalization_action and entry.recommended_action == "pending_review":
                    mapped = time_action_map.get(existing.rationalization_action.upper())
                    if mapped:
                        entry.recommended_action = mapped
                skipped_count += 1
                continue

            # Auto-calculate TIME scores from available metadata
            # Technical Health (30%)
            tech_score = 50  # neutral default
            lifecycle = (app.lifecycle_status or "").lower()
            if "decommission" in lifecycle or "retired" in lifecycle:
                tech_score = 15
            elif "deprecated" in lifecycle:
                tech_score = 30
            elif "operational" in lifecycle or "active" in lifecycle:
                tech_score = 65

            # Business Value (35%)
            biz_score = 50
            criticality = (getattr(app, "strategic_importance", "") or getattr(app, "business_criticality", "") or "").lower()
            if criticality in ("critical", "mission_critical"):
                biz_score = 85
            elif criticality == "high":
                biz_score = 70
            elif criticality in ("medium", "important"):
                biz_score = 50
            elif criticality in ("low", "supporting"):
                biz_score = 30

            user_count = app.user_count or 0
            if user_count > 500:
                biz_score = min(biz_score + 15, 100)
            elif user_count < 10:
                biz_score = max(biz_score - 15, 0)

            # Cost Efficiency (25%)
            cost_score = 50
            tco = float(app.total_cost_of_ownership or 0)
            if tco > 0:
                cost_per_user = tco / max(user_count, 1)
                if cost_per_user > 5000:
                    cost_score = 25
                elif cost_per_user < 500:
                    cost_score = 80

            # Vendor Risk (10%)
            vendor_score = 60  # neutral
            vendor_type = (getattr(app, "vendor_type", "") or "").lower()
            if vendor_type == "internal":
                vendor_score = 80
            elif vendor_type == "open_source":
                vendor_score = 70

            # Overall score
            overall = int(tech_score * 0.30 + biz_score * 0.35 + cost_score * 0.25 + vendor_score * 0.10)

            # Determine TIME action
            if overall < 40 or (biz_score < 35 and cost_score < 40):
                action = "ELIMINATE"
            elif tech_score < 40 and biz_score > 50:
                action = "MIGRATE"
            elif biz_score > 70 and tech_score > 50:
                action = "INVEST"
            else:
                action = "TOLERATE"

            # Create score record
            score = ApplicationRationalizationScore(
                application_component_id=app.id,
                assessment_date=datetime.utcnow().date(),
                technical_health_score=tech_score,
                business_value_score=biz_score,
                cost_efficiency_score=cost_score,
                vendor_risk_score=vendor_score,
                overall_health_score=overall,
                rationalization_action=action,
                action_rationale=f"Auto-scored from metadata: lifecycle={app.lifecycle_status}, criticality={criticality}",
                ai_generated=True,
                ai_model_used="metadata_auto_scorer_v1",
                scoring_model_version="1.0",
            )
            db.session.add(score)

            # Update consolidation entry action
            mapped_action = time_action_map.get(action, "pending_review")
            entry.recommended_action = mapped_action
            entry.updated_at = datetime.utcnow()
            scored_count += 1

        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"Scored {scored_count} applications, {skipped_count} already scored",
            "scored_count": scored_count,
            "skipped_count": skipped_count,
        })

    except HTTPException:

        raise

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in score-all: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500
