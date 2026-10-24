"""SAFe Portfolio Kanban board routes."""

import logging
from typing import Dict, List

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.core.auth.decorators import require_permission
from app.core.auth.permissions import can
from app.middleware.tenant_context import current_org_id
from app.models.archimate_core import ArchiMateElement
from app.services.element_profile_service import read_profile, set_wsjf_component, get_kind_property, set_kind_property
from app.services.wsjf_service import compute_wsjf_score
from app.services.profiles.safe import KINDS

from . import safe_portfolio_bp, safe_portfolio_api_bp

logger = logging.getLogger(__name__)

# Kanban column order — sourced from the one SAFe profile registry
KANBAN_STATES = KINDS["Epic"]["properties"]["kanban_state"]["values"]

# SAFe portfolio WIP limits per column. Funnel and Done have no limit; the four
# middle columns do (recognisable-form reference, Portfolio Kanban conventions).
# The header shows "count / limit" and turns to the destructive token with a
# lock icon when a column is at its limit.
WIP_LIMITS = {
    "Reviewing": 4,
    "Analyzing": 3,
    "Portfolio Backlog": 6,
    "Implementing": 5,
}

# Which band each column belongs to, so the shared board can tint its headers:
# discovery stages in the info/blue token, execution stages in the
# success/green token, Done neutral.
STAGE_GROUP = {
    "Funnel": "discovery",
    "Reviewing": "discovery",
    "Analyzing": "discovery",
    "Portfolio Backlog": "execution",
    "Implementing": "execution",
    "Done": "done",
}

# What an empty column means and what to do next, per state. A state with no
# entry here falls back to a plain sentence in the template.
EMPTY_COLUMN_HINTS = {
    "Funnel": {
        "why": "No new epics are waiting.",
        "next_write": "Mark a work package as a SAFe Epic and it arrives here.",
        "next_read": "A work package marked as a SAFe Epic arrives here.",
    },
    "Reviewing": {
        "why": "No epics are being reviewed.",
        "next_write": "Move an epic here from Funnel when its business case is ready.",
        "next_read": "Someone with edit access moves an epic here from Funnel when its business case is ready.",
    },
    "Analyzing": {
        "why": "No epics are being analysed.",
        "next_write": "Move an epic here from Reviewing once it is approved for analysis.",
        "next_read": "Someone with edit access moves an epic here from Reviewing once it is approved for analysis.",
    },
    "Portfolio Backlog": {
        "why": "No analysed epics are waiting for capacity.",
        "next_write": "Move an epic here from Analyzing once its analysis is done.",
        "next_read": "Someone with edit access moves an epic here from Analyzing once its analysis is done.",
    },
    "Implementing": {
        "why": "No epics are being implemented.",
        "next_write": "Move an epic here from Portfolio Backlog when a team starts work on it.",
        "next_read": "Someone with edit access moves an epic here from Portfolio Backlog when a team starts work on it.",
    },
    "Done": {
        "why": "No epics have been delivered.",
        "next_write": "Move an epic here from Implementing when its outcome is delivered.",
        "next_read": "Someone with edit access moves an epic here from Implementing when its outcome is delivered.",
    },
}


def _can_write() -> bool:
    """Whether the signed-in user may edit work packages, per the permission matrix."""
    return can(current_user, "edit", "motivation_element")


def _get_epics() -> List[Dict]:
    """Load all Epic work packages for the current organization."""
    org_id = current_org_id()
    if org_id is None:
        return []

    elements = (
        db.session.query(ArchiMateElement)
        .filter(
            ArchiMateElement.organization_id == org_id,
            ArchiMateElement.type == "WorkPackage",
        )
        .all()
    )

    epics = []
    for element in elements:
        profile = read_profile(element)
        if profile.get("kind") != "Epic":
            continue

        # Read kanban state from acm_properties
        kanban_state = get_kind_property(element, "kanban_state") or "Funnel"

        # Get WSJF components
        props = profile.get("properties", {})
        bv = props.get("business_value", {}).get("value", 1) or 1
        tc = props.get("time_criticality", {}).get("value", 1) or 1
        rr = props.get("risk_reduction", {}).get("value", 1) or 1
        js = props.get("job_size", {}).get("value", 1) or 1

        wsjf_score = compute_wsjf_score(
            business_value=bv,
            time_criticality=tc,
            risk_reduction=rr,
            job_size=js,
        )

        # Get owner name if available
        owner_name = None
        created_by_id = getattr(element, "created_by_id", None)
        if created_by_id:
            from app.models.user import User
            owner = db.session.query(User).filter_by(id=created_by_id).first()
            if owner:
                owner_name = f"{owner.first_name} {owner.last_name}".strip() or owner.email

        # Format date
        date_str = None
        stamp = getattr(element, "updated_at", None) or getattr(element, "created_at", None)
        if stamp:
            date_str = stamp.strftime("%Y-%m-%d")

        epics.append(
            {
                "id": element.id,
                "name": element.name,
                "description": element.description or "",
                # `state` is the column key the shared board component reads;
                # `kanban_state` is kept for the PATCH endpoint and any caller
                # that still reads it.
                "state": kanban_state,
                "kanban_state": kanban_state,
                "wsjf_score": wsjf_score,
                "business_value": bv,
                "time_criticality": tc,
                "risk_reduction": rr,
                "job_size": js,
                "owner_name": owner_name,
                "updated_at": date_str,
            }
        )

    return epics


def _organize_by_state(epics: List[Dict]) -> Dict[str, List[Dict]]:
    """Organize epics into columns by kanban state, sorted by WSJF descending."""
    columns = {state: [] for state in KANBAN_STATES}

    for epic in epics:
        state = epic["kanban_state"]
        if state in columns:
            columns[state].append(epic)

    # Sort each column by WSJF score descending
    for state in columns:
        columns[state].sort(key=lambda e: e["wsjf_score"], reverse=True)

    return columns


@safe_portfolio_bp.route("/")
@login_required
def index():
    """Portfolio Kanban board view."""
    epics = _get_epics()
    can_write = _can_write()

    columns_config = [
        {"id": state, "label": state, "state": state} for state in KANBAN_STATES
    ]

    return render_template(
        "safe_portfolio/kanban.html",
        cards=epics,
        columns_config=columns_config,
        kanban_states=KANBAN_STATES,
        wip_limits=WIP_LIMITS,
        stage_group=STAGE_GROUP,
        empty_hints=EMPTY_COLUMN_HINTS,
        can_write=can_write,
    )


@safe_portfolio_api_bp.route("")
@login_required
def api_list():
    """JSON API for portfolio epics."""
    epics = _get_epics()
    columns = _organize_by_state(epics)

    return jsonify(
        {
            "columns": columns,
            "states": KANBAN_STATES,
            "can_write": _can_write(),
        }
    )


@safe_portfolio_api_bp.route("/<int:element_id>", methods=["PATCH"])
@login_required
@require_permission("edit", "motivation_element")
def api_update(element_id: int):
    """Update an epic's kanban state or WSJF components."""
    org_id = current_org_id()
    if org_id is None:
        abort(404)

    element = db.session.execute(
        db.select(ArchiMateElement).where(
            ArchiMateElement.id == element_id,
            ArchiMateElement.organization_id == org_id,
            ArchiMateElement.type == "WorkPackage",
        )
    ).scalar_one_or_none()

    if element is None:
        return jsonify({"error": "Epic not found"}), 404

    # Verify it's an Epic
    profile = read_profile(element)
    if profile.get("kind") != "Epic":
        return jsonify({"error": "Element is not an Epic"}), 400

    data = request.get_json() or {}

    # Update kanban state
    if "kanban_state" in data:
        new_state = data["kanban_state"]
        if new_state not in KANBAN_STATES:
            return jsonify({"error": "Invalid kanban state"}), 400
        set_kind_property(element, "kanban_state", new_state, source="user")

    # Update WSJF components
    if "business_value" in data:
        set_wsjf_component(element, "business_value", data["business_value"])
    if "time_criticality" in data:
        set_wsjf_component(element, "time_criticality", data["time_criticality"])
    if "risk_reduction" in data:
        set_wsjf_component(element, "risk_reduction", data["risk_reduction"])
    if "job_size" in data:
        set_wsjf_component(element, "job_size", data["job_size"])

    db.session.commit()

    # Return updated profile
    updated_profile = read_profile(element)
    kanban_state = get_kind_property(element, "kanban_state") or "Funnel"

    props = updated_profile.get("properties", {})
    bv = props.get("business_value", {}).get("value", 1) or 1
    tc = props.get("time_criticality", {}).get("value", 1) or 1
    rr = props.get("risk_reduction", {}).get("value", 1) or 1
    js = props.get("job_size", {}).get("value", 1) or 1

    wsjf_score = compute_wsjf_score(
        business_value=bv,
        time_criticality=tc,
        risk_reduction=rr,
        job_size=js,
    )

    return jsonify(
        {
            "id": element.id,
            "name": element.name,
            "kanban_state": kanban_state,
            "wsjf_score": wsjf_score,
            "business_value": bv,
            "time_criticality": tc,
            "risk_reduction": rr,
            "job_size": js,
        }
    )
