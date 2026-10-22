"""Team routes — list, detail, create, edit, and allocation management.

Blueprint: teams_bp  |  URL prefix: /teams
"""

import logging
from datetime import date

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.middleware.tenant_context import current_org_id
from app.middleware.tenant_decorators import require_org_or_platform_admin
from app.services.team_service import (
    AllocationOverflow,
    create_team,
    delete_allocation,
    delete_team,
    derive_allocations_for_team,
    get_allocations_for_team,
    get_team,
    get_teams_for_organization,
    get_team_total_share,
    record_allocation,
    update_allocation,
    update_team,
)

logger = logging.getLogger(__name__)

teams_bp = Blueprint("teams", __name__, template_folder="../../templates/teams")


def _org_id():
    """Return the current org id or abort."""
    org_id = current_org_id()
    if org_id is None:
        abort(403)
    return org_id


# ── Team list ────────────────────────────────────────────────────────────────


@teams_bp.route("/teams")
@login_required
def team_list():
    """List all teams in the current organisation."""
    org_id = _org_id()
    teams = get_teams_for_organization(org_id)
    return render_template("teams/team_list.html", teams=teams)


# ── Team detail ──────────────────────────────────────────────────────────────


@teams_bp.route("/teams/<int:team_id>")
@login_required
def team_detail(team_id):
    """Show a team's detail page with its allocations."""
    org_id = _org_id()
    team = get_team(team_id, org_id)
    if team is None:
        abort(404)
    allocations = get_allocations_for_team(team_id, org_id)
    total_share = get_team_total_share(team_id, org_id)
    unallocated = max(0, 100 - total_share)

    # Resolve element names for display
    resolved = []
    for alloc in allocations:
        element_name = _resolve_element_name(alloc.element_type, alloc.element_id)
        resolved.append({"allocation": alloc, "element_name": element_name})

    return render_template(
        "teams/team_detail.html",
        team=team,
        allocations=resolved,
        total_share=total_share,
        unallocated=unallocated,
    )


# ── Create team ──────────────────────────────────────────────────────────────


@teams_bp.route("/teams/create", methods=["GET", "POST"])
@login_required
@require_org_or_platform_admin
def team_create():
    """Create a new team."""
    org_id = _org_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Team name is required.", "error")
            return render_template("teams/team_form.html", team=None)
        description = request.form.get("description", "").strip()
        try:
            headcount = int(request.form.get("headcount", 0) or 0)
        except (ValueError, TypeError):
            headcount = 0
        team = create_team(
            name=name,
            organization_id=org_id,
            description=description,
            headcount=headcount,
        )
        flash(f"Team '{team.name}' created.", "success")
        return redirect(url_for("teams.team_detail", team_id=team.id))
    return render_template("teams/team_form.html", team=None)


# ── Edit team ────────────────────────────────────────────────────────────────


@teams_bp.route("/teams/<int:team_id>/edit", methods=["GET", "POST"])
@login_required
@require_org_or_platform_admin
def team_edit(team_id):
    """Edit a team."""
    org_id = _org_id()
    team = get_team(team_id, org_id)
    if team is None:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Team name is required.", "error")
            return render_template("teams/team_form.html", team=team)
        description = request.form.get("description", "").strip()
        try:
            headcount = int(request.form.get("headcount", 0) or 0)
        except (ValueError, TypeError):
            headcount = 0
        update_team(
            team_id=team_id,
            organization_id=org_id,
            name=name,
            description=description,
            headcount=headcount,
        )
        flash("Team updated.", "success")
        return redirect(url_for("teams.team_detail", team_id=team_id))
    return render_template("teams/team_form.html", team=team)


# ── Delete team ──────────────────────────────────────────────────────────────


@teams_bp.route("/teams/<int:team_id>/delete", methods=["POST"])
@login_required
@require_org_or_platform_admin
def team_delete(team_id):
    """Delete a team."""
    org_id = _org_id()
    if delete_team(team_id, org_id):
        flash("Team deleted.", "success")
    else:
        flash("Team not found.", "error")
    return redirect(url_for("teams.team_list"))


# ── Allocations ──────────────────────────────────────────────────────────────


@teams_bp.route("/teams/<int:team_id>/allocations/add", methods=["POST"])
@login_required
@require_org_or_platform_admin
def allocation_add(team_id):
    """Add an allocation to a team."""
    org_id = _org_id()
    team = get_team(team_id, org_id)
    if team is None:
        abort(404)

    element_type = request.form.get("element_type", "").strip()
    try:
        element_id = int(request.form.get("element_id", 0))
    except (ValueError, TypeError):
        element_id = 0
    try:
        share_percent = int(request.form.get("share_percent", 0))
    except (ValueError, TypeError):
        share_percent = 0

    if not element_type or element_id <= 0 or share_percent <= 0:
        flash("Invalid allocation parameters.", "error")
        return redirect(url_for("teams.team_detail", team_id=team_id))

    try:
        record_allocation(
            team_id=team_id,
            element_type=element_type,
            element_id=element_id,
            share_percent=share_percent,
            organization_id=org_id,
            source="recorded",
        )
        flash("Allocation added.", "success")
    except AllocationOverflow as exc:
        flash(str(exc), "error")
    except ValueError as exc:
        flash(str(exc), "error")

    return redirect(url_for("teams.team_detail", team_id=team_id))


@teams_bp.route("/teams/<int:team_id>/allocations/<int:allocation_id>/edit", methods=["POST"])
@login_required
@require_org_or_platform_admin
def allocation_edit(team_id, allocation_id):
    """Edit an allocation's share percent."""
    org_id = _org_id()
    try:
        share_percent = int(request.form.get("share_percent", 0))
    except (ValueError, TypeError):
        share_percent = 0

    if share_percent <= 0 or share_percent > 100:
        flash("Share must be between 1 and 100.", "error")
        return redirect(url_for("teams.team_detail", team_id=team_id))

    try:
        result = update_allocation(
            allocation_id=allocation_id,
            organization_id=org_id,
            share_percent=share_percent,
        )
        if result:
            flash("Allocation updated.", "success")
        else:
            flash("Allocation not found.", "error")
    except AllocationOverflow as exc:
        flash(str(exc), "error")

    return redirect(url_for("teams.team_detail", team_id=team_id))


@teams_bp.route("/teams/<int:team_id>/allocations/<int:allocation_id>/delete", methods=["POST"])
@login_required
@require_org_or_platform_admin
def allocation_delete(team_id, allocation_id):
    """Delete an allocation."""
    org_id = _org_id()
    if delete_allocation(allocation_id, org_id):
        flash("Allocation removed.", "success")
    else:
        flash("Allocation not found.", "error")
    return redirect(url_for("teams.team_detail", team_id=team_id))


@teams_bp.route("/teams/<int:team_id>/derive", methods=["POST"])
@login_required
@require_org_or_platform_admin
def derive_allocations(team_id):
    """Derive technology allocations from owned applications."""
    org_id = _org_id()
    derived = derive_allocations_for_team(team_id, org_id)
    flash(f"Derived {len(derived)} technology allocation(s) from owned applications.", "success")
    return redirect(url_for("teams.team_detail", team_id=team_id))


# ── API: list teams (for selectors) ──────────────────────────────────────────


@teams_bp.route("/api/teams")
@login_required
def api_team_list():
    """Return teams as JSON for selectors."""
    org_id = _org_id()
    teams = get_teams_for_organization(org_id)
    return jsonify([
        {"id": t.id, "name": t.name, "headcount": t.headcount or 0}
        for t in teams
    ])


@teams_bp.route("/api/teams/<int:team_id>/allocations")
@login_required
def api_team_allocations(team_id):
    """Return a team's allocations as JSON."""
    org_id = _org_id()
    allocations = get_allocations_for_team(team_id, org_id)
    return jsonify([
        {
            "id": a.id,
            "element_type": a.element_type,
            "element_id": a.element_id,
            "element_name": _resolve_element_name(a.element_type, a.element_id),
            "share_percent": a.share_percent,
            "source": a.source,
        }
        for a in allocations
    ])


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_element_name(element_type: str, element_id: int) -> str:
    """Resolve an element's name for display."""
    if element_type == "application":
        from app.models.application_portfolio import ApplicationComponent
        app_obj = ApplicationComponent.query.get(element_id)
        return app_obj.name if app_obj else f"Application #{element_id}"
    elif element_type == "technology":
        from app.models.archimate_core import ArchiMateElement
        el = db.session.get(ArchiMateElement, element_id)
        return el.name if el else f"Technology #{element_id}"
    return f"{element_type} #{element_id}"