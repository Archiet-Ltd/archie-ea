"""History routes for the intelligence module.

Registered on the existing `intelligence_ui` blueprint (not a new blueprint on
the same prefix — ADR 0008 rule 3). Provides:

- GET /intelligence/history/as-of — "the model as of a date" page
- GET /intelligence/history/changes — "what changed between two dates" page
- GET /intelligence/api/history/as-of — JSON API for as-of snapshot
- GET /intelligence/api/history/changes — JSON API for difference
- GET /intelligence/api/history/element/<element_id> — JSON API for one element's history panel
"""
from __future__ import annotations

from datetime import datetime

from flask import render_template, request
from flask_login import login_required

from app.modules.intelligence.services.history_service import HistoryService
from app.utils.api_response import error_response, success_response
from app.utils.tenant import current_organization_id

# Reuse the existing intelligence_ui blueprint from ui.py
from app.modules.intelligence.routes.ui import intelligence_ui


def _parse_datetime_param(raw: str | None, param_name: str) -> tuple[datetime | None, dict | None]:
    """Parse an ISO datetime parameter. Returns (datetime, error_response_or_None)."""
    if raw is None:
        return None, error_response(
            f"{param_name} is required",
            code="MISSING_PARAMETER",
            status_code=400,
        )
    try:
        # Accept both date-only and full datetime
        if "T" in raw:
            return datetime.fromisoformat(raw), None
        return datetime.fromisoformat(raw + "T00:00:00"), None
    except ValueError:
        return None, error_response(
            f"{param_name} must be a valid ISO date or datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)",
            code="INVALID_PARAMETER",
            status_code=400,
        )


# ----------------------------------------------------------------------
# UI Routes (render templates)
# ----------------------------------------------------------------------


@intelligence_ui.route("/history/as-of", methods=["GET"])
@login_required
def history_as_of_page():
    """Render the 'model as of a date' page.

    Query params:
    - as_of (optional): ISO date/datetime. Defaults to now.
    """
    as_of_raw = request.args.get("as_of")
    if as_of_raw:
        as_of_date, err = _parse_datetime_param(as_of_raw, "as_of")
        if err:
            return err
    else:
        as_of_date = datetime.utcnow()

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    from app.extensions import db
    service = HistoryService(db.session, organization_id)
    result = service.as_of(as_of_date)

    return render_template(
        "intelligence/history_as_of.html",
        as_of_date=as_of_date,
        elements=result.elements,
        relationships=result.relationships,
    )


@intelligence_ui.route("/history/changes", methods=["GET"])
@login_required
def history_changes_page():
    """Render the 'changes between dates' page.

    Query params:
    - from (required): ISO date/datetime
    - to (required): ISO date/datetime
    """
    from_raw = request.args.get("from")
    to_raw = request.args.get("to")

    from_date, err = _parse_datetime_param(from_raw, "from")
    if err:
        return err
    to_date, err = _parse_datetime_param(to_raw, "to")
    if err:
        return err

    if from_date >= to_date:
        return error_response(
            "from must be before to",
            code="INVALID_RANGE",
            status_code=400,
        )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    from app.extensions import db
    service = HistoryService(db.session, organization_id)
    result = service.difference(from_date, to_date)

    return render_template(
        "intelligence/history_changes.html",
        from_date=from_date,
        to_date=to_date,
        changes=result.changes,
    )


# ----------------------------------------------------------------------
# API Routes (JSON)
# ----------------------------------------------------------------------


@intelligence_ui.route("/api/history/as-of", methods=["GET"])
@login_required
def history_as_of_api():
    """JSON API: the model as of a date.

    Query params:
    - as_of (required): ISO date/datetime

    Returns:
    {
        "as_of": "ISO datetime",
        "elements": [...],
        "relationships": [...]
    }
    """
    as_of_raw = request.args.get("as_of")
    as_of_date, err = _parse_datetime_param(as_of_raw, "as_of")
    if err:
        return err

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    from app.extensions import db
    service = HistoryService(db.session, organization_id)
    result = service.as_of(as_of_date)

    return success_response({
        "as_of": result.as_of.isoformat(),
        "elements": [
            {
                "id": e.id,
                "name": e.name,
                "type": e.type,
                "layer": e.layer,
                "organization_id": e.organization_id,
                "valid_from": e.valid_from.isoformat(),
                "valid_to": e.valid_to.isoformat() if e.valid_to else None,
                "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
                "snapshot": e.snapshot,
            }
            for e in result.elements
        ],
        "relationships": [
            {
                "id": r.id,
                "type": r.type,
                "source_id": r.source_id,
                "target_id": r.target_id,
                "organization_id": r.organization_id,
                "valid_from": r.valid_from.isoformat(),
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
                "snapshot": r.snapshot,
            }
            for r in result.relationships
        ],
    })


@intelligence_ui.route("/api/history/changes", methods=["GET"])
@login_required
def history_changes_api():
    """JSON API: what changed between two dates.

    Query params:
    - from (required): ISO date/datetime
    - to (required): ISO date/datetime

    Returns:
    {
        "from": "ISO datetime",
        "to": "ISO datetime",
        "changes": [...]
    }
    """
    from_raw = request.args.get("from")
    to_raw = request.args.get("to")

    from_date, err = _parse_datetime_param(from_raw, "from")
    if err:
        return err
    to_date, err = _parse_datetime_param(to_raw, "to")
    if err:
        return err

    if from_date >= to_date:
        return error_response(
            "from must be before to",
            code="INVALID_RANGE",
            status_code=400,
        )

    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    from app.extensions import db
    service = HistoryService(db.session, organization_id)
    result = service.difference(from_date, to_date)

    return success_response({
        "from": result.from_date.isoformat(),
        "to": result.to_date.isoformat(),
        "changes": [
            {
                "table_name": c.table_name,
                "record_id": c.record_id,
                "action": c.action,
                "valid_from": c.valid_from.isoformat(),
                "valid_to": c.valid_to.isoformat() if c.valid_to else None,
                "recorded_at": c.recorded_at.isoformat() if c.recorded_at else None,
                "changed_by_id": c.changed_by_id,
                "changed_by_name": c.changed_by_name,
                "change_reason": c.change_reason,
                "snapshot": c.snapshot,
                "previous_snapshot": c.previous_snapshot,
            }
            for c in result.changes
        ],
    })


@intelligence_ui.route("/api/history/element/<int:element_id>", methods=["GET"])
@login_required
def history_element_api(element_id: int):
    """JSON API: full history for one element (for the history panel).

    Returns all versions of the element, ordered by valid_from, with audit log
    join for who/why on each version.
    """
    organization_id = current_organization_id()
    if organization_id is None:
        return error_response(
            "no tenant context for this request",
            code="NO_TENANT_CONTEXT",
            status_code=400,
        )

    from app.extensions import db
    from app.models.entity_history import EntityHistory
    from app.models.audit_log import AuditLog

    # Fetch all versions for this element in this org
    versions = db.session.execute(
        db.select(EntityHistory).where(
            EntityHistory.organization_id == organization_id,
            EntityHistory.table_name == "archimate_elements",
            EntityHistory.record_id == element_id,
        ).order_by(EntityHistory.valid_from)
    ).scalars().all()

    if not versions:
        return error_response("Element not found", code="NOT_FOUND", status_code=404)

    # Fetch audit entries for this element
    audit_entries = db.session.execute(
        db.select(AuditLog).where(
            AuditLog.organization_id == organization_id,
            AuditLog.table_name == "archimate_elements",
            AuditLog.record_id == element_id,
        ).order_by(AuditLog.created_at)
    ).scalars().all()

    # Index audit by created_at for matching
    audit_by_time = {entry.created_at: entry for entry in audit_entries}

    versions_out = []
    for i, v in enumerate(versions):
        # Match audit entry by closest created_at to recorded_at
        audit_match = None
        if v.recorded_at and audit_by_time:
            audit_match = min(
                audit_entries,
                key=lambda e: abs((e.created_at - v.recorded_at).total_seconds()),
            )

        changed_by_name = None
        change_reason = None
        if audit_match:
            change_reason = audit_match.new_value.get("change_reason") if isinstance(audit_match.new_value, dict) else None
            if audit_match.user_id:
                try:
                    from app.models.user import User
                    user = db.session.get(User, audit_match.user_id)
                    if user:
                        changed_by_name = user.email or user.username or str(user.id)
                except Exception:
                    changed_by_name = str(audit_match.user_id)

        # Determine action
        is_first = i == 0
        is_last = i == len(versions) - 1 and v.valid_to is not None
        if is_first and v.valid_from is not None:
            action = "insert"
        elif is_last:
            action = "delete"
        else:
            action = "update"

        previous_snapshot = versions[i - 1].snapshot if action == "update" and i > 0 else None

        versions_out.append({
            "version_index": i,
            "action": action,
            "valid_from": v.valid_from.isoformat() if v.valid_from else None,
            "valid_to": v.valid_to.isoformat() if v.valid_to else None,
            "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
            "superseded_at": v.superseded_at.isoformat() if v.superseded_at else None,
            "source": v.source,
            "changed_by_id": audit_match.user_id if audit_match else None,
            "changed_by_name": changed_by_name,
            "change_reason": change_reason,
            "snapshot": v.snapshot,
            "previous_snapshot": previous_snapshot,
        })

    return success_response({
        "element_id": element_id,
        "versions": versions_out,
    })


__all__ = ["intelligence_ui"]