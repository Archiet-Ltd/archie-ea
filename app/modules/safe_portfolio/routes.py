"""SAFe Portfolio Kanban board routes."""

import logging
from typing import Dict, List

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.core.auth.permissions import may_change_model_element
from app.middleware.tenant_context import current_org_id
from app.models.archimate_core import ArchiMateElement
from app.services.element_profile_service import (
    get_kind_property,
    read_profile,
    set_kind,
    set_kind_property,
    set_wsjf_component,
    set_wsjf_components,
)
from app.models.application_owner import ApplicationOwner
from app.services.element_ownership_service import set_element_owner
from app.services.archimate_backbone import create_backbone_element
from app.services.wsjf_service import compute_wsjf_score
from app.services.profiles.safe import KINDS

from . import safe_portfolio_bp, safe_portfolio_api_bp

logger = logging.getLogger(__name__)

# Kanban column order — sourced from the one SAFe profile registry
KANBAN_STATES = KINDS["Epic"]["properties"]["kanban_state"]["values"]
EPIC_TYPES = KINDS["Epic"]["properties"]["epic_type"]["values"]

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

# The rule for leaving each column, shown in muted text under its header. These
# are the exit criteria of SAFe's Portfolio Kanban in plain words.
EXIT_RULES = {
    "Funnel": "Epic idea captured with its hypothesis",
    "Reviewing": "Hypothesis refined and ready for analysis",
    "Analyzing": "Lean business case and WSJF ready",
    "Portfolio Backlog": "Approved and waiting for capacity",
    "Implementing": "Minimum viable product released",
    "Done": "Outcome delivered or the epic stopped",
}

# Columns that are kept in a stated order, and the words that say so.
SORT_NOTES = {"Portfolio Backlog": "Sorted by WSJF"}

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


@safe_portfolio_bp.route("/epics", methods=["POST"])
@login_required
def add_epic():
    """Record a new Epic in the Funnel from the board's Add epic form."""
    if not _can_write():
        abort(403)
    org_id = current_org_id()
    if org_id is None:
        abort(404)

    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Give the epic a name before adding it.", "error")
        return redirect(url_for("safe_portfolio.index"))

    def _score(field):
        try:
            return min(max(int(request.form.get(field) or 1), 1), 20)
        except ValueError:
            return 1

    element = create_backbone_element(
        element_type="WorkPackage",
        layer="Implementation",
        name=name,
        description=(request.form.get("description") or "").strip() or None,
        organization_id=org_id,
    )
    set_kind(element, "Epic", framework_id="safe", source="user")
    set_kind_property(element, "kanban_state", KANBAN_STATES[0], source="user")
    epic_type = (request.form.get("epic_type") or "").strip()
    if epic_type in EPIC_TYPES:
        set_kind_property(element, "epic_type", epic_type, source="user")
    set_wsjf_components(
        element,
        business_value=_score("business_value"),
        time_criticality=_score("time_criticality"),
        risk_reduction=_score("risk_reduction"),
        job_size=_score("job_size"),
        source="user",
    )
    db.session.flush()
    set_element_owner(
        element_type="epic", element_id=element.id, user_id=current_user.id,
        organization_id=org_id, assigned_by=current_user.id, commit=False,
    )
    db.session.commit()
    flash(f"Added {name} to the {KANBAN_STATES[0]} column.", "success")
    return redirect(url_for("safe_portfolio.index"))


def _can_write() -> bool:
    """Whether the signed-in user may edit work packages, per the permission matrix."""
    return may_change_model_element(current_user, "edit")


def _can_open_team(org_id: int) -> bool:
    """Whether the signed-in user may open the team page, where people are listed."""
    from app.middleware.tenant_decorators import is_platform_admin
    from app.services.rbac_service import rbac_service

    return bool(is_platform_admin(current_user) or rbac_service.is_org_admin(current_user, org_id))


def _initials(name):
    return "".join(part[0] for part in (name or "").split()[:2]).upper()


def _owner_name(element: ArchiMateElement, org_id: int):
    """Name of the person who recorded the element by hand, if they belong to this organization."""
    ref = (element.source_ref or "").strip()
    if element.source_kind != "manual" or not ref.isdigit():
        return None
    from app.models.user import User

    owner = (
        db.session.query(User)
        .filter(User.id == int(ref), User.organization_id == org_id)
        .first()
    )
    if owner is None:
        return None
    return f"{owner.first_name} {owner.last_name}".strip() or owner.email


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

    epic_elements = [e for e in elements if read_profile(e).get("kind") == "Epic"]
    # Owners come from the one owner record, read in one query for the board.
    owners = ApplicationOwner.get_display_rows_for_elements_batch(
        "epic", [e.id for e in epic_elements], org_id
    )
    # A person's name links to the team page only for someone who can open it.
    owner_href = url_for("team.team") if _can_open_team(org_id) else None

    epics = []
    for element in epic_elements:
        profile = read_profile(element)

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
        rows = sorted(
            owners.get(element.id, []),
            key=lambda row: (row["ownership_type"] != "primary", row["id"]),
        )
        owner_name = rows[0]["user_name"] if rows else _owner_name(element, org_id)
        owner_name = owner_name if owner_name and owner_name != "\u2014" else None

        # Format date
        recorded = element.source_recorded_at or element.recorded_at
        date_str = recorded.strftime("%Y-%m-%d") if recorded else None

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
                "owner_initials": _initials(owner_name),
                "owner_linked": bool(owner_name and owner_href),
                "owner_plain": bool(owner_name and not owner_href),
                "owner_missing": not owner_name,
                "owner_href": owner_href,
                "epic_type": get_kind_property(element, "epic_type"),
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
        exit_rules=EXIT_RULES,
        sort_notes=SORT_NOTES,
        epic_types=EPIC_TYPES,
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
def api_update(element_id: int):
    """Update an epic's kanban state or WSJF components."""
    if not _can_write():
        return jsonify({"error": "Your role can view this board but not change it."}), 403
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
        limit = WIP_LIMITS.get(new_state, 0)
        if limit and get_kind_property(element, "kanban_state") != new_state:
            in_column = sum(1 for epic in _get_epics() if epic["state"] == new_state)
            if in_column >= limit:
                return jsonify({
                    "error": f"{new_state} is at its limit of {limit} epics. "
                             "Finish or move one on before adding another."
                }), 409
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
