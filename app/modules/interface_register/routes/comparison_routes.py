"""Routes for the Interface Register comparison functionality.

Blueprint: interface_register_bp, url_prefix="/interface-register".
"""

from __future__ import annotations

from flask import (
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from app.models.implementation_migration import Gap, GAP_KIND_PLATEAU_TRANSITION
from app.modules.interface_register.routes.register_routes import (
    interface_register_bp,
    _guard,
)
from app.modules.interface_register.services import (
    interface_register_service as service,
    plateau_pair_service,
    interface_gap_service,
    work_package_service,
)
from app.utils.plain_errors import render_plain_error


@interface_register_bp.route("/comparison", methods=["GET"])
@login_required
def comparison():
    """Display the as-is/to-be comparison view."""
    guard = _guard()
    if guard:
        return guard

    initiative_id = request.args.get("initiative_id", type=int)
    if not initiative_id:
        return redirect(url_for("interface_register.index"))

    try:
        initiative = service.resolve_initiative(initiative_id, g.current_org_id)
    except service.InterfaceRegisterError:
        return render_plain_error(404)

    # Get plateau pair (if exists)
    plateau_pair = plateau_pair_service.get_plateau_pair(initiative_id)
    as_is = plateau_pair[0] if plateau_pair else None
    to_be = plateau_pair[1] if plateau_pair else None

    # Get interfaces
    interfaces = service.list_interfaces(initiative_id, g.current_org_id)

    # Get gaps (if to-be plateau exists)
    if to_be:
        gaps = Gap.query.filter_by(
            target_plateau_id=to_be.id,
            gap_kind=GAP_KIND_PLATEAU_TRANSITION
        ).order_by(Gap.created_at.desc()).all()
    else:
        gaps = []

    return render_template(
        "interface_register/comparison.html",
        initiative=initiative,
        as_is=as_is,
        to_be=to_be,
        interfaces=interfaces,
        gaps=gaps,
        gap_types=interface_gap_service.GAP_TYPES,
        errors=None,
    )


@interface_register_bp.route("/comparison/provision", methods=["POST"])
@login_required
def provision_comparison():
    """Provision the as-is/to-be plateau pair."""
    guard = _guard()
    if guard:
        return guard

    initiative_id = request.form.get("initiative_id", type=int)
    if not initiative_id:
        flash("An initiative is required to set up comparison.", "error")
        return redirect(url_for("interface_register.index"))

    try:
        service.resolve_initiative(initiative_id, g.current_org_id)
    except service.InterfaceRegisterError:
        return render_plain_error(404)

    try:
        plateau_pair_service.provision_plateau_pair(initiative_id)
    except service.InterfaceRegisterError as exc:
        flash(str(exc), "error")
        return redirect(url_for("interface_register.index", initiative_id=initiative_id))

    flash("As-is/To-be comparison set up successfully.", "success")
    return redirect(url_for("interface_register.comparison", initiative_id=initiative_id))


@interface_register_bp.route("/<int:element_id>/gaps", methods=["POST"])
@login_required
def raise_gap(element_id):
    """Raise a gap for an interface."""
    guard = _guard()
    if guard:
        return guard

    initiative_id = request.form.get("initiative_id", type=int)
    gap_type = request.form.get("gap_type")

    try:
        initiative = service.resolve_initiative(initiative_id, g.current_org_id)
    except service.InterfaceRegisterError:
        return render_plain_error(404)

    try:
        interface_gap_service.raise_interface_gap(
            element_id,
            initiative_id,
            gap_type,
            name=request.form.get("name"),
            description=request.form.get("description"),
            severity=request.form.get("severity")
        )
        flash("Gap raised successfully.", "success")
        return redirect(url_for("interface_register.comparison", initiative_id=initiative_id))
    except service.InterfaceRegisterError as exc:
        # Re-render the comparison page with errors
        plateau_pair = plateau_pair_service.get_plateau_pair(initiative_id)
        as_is = plateau_pair[0] if plateau_pair else None
        to_be = plateau_pair[1] if plateau_pair else None
        interfaces = service.list_interfaces(initiative_id, g.current_org_id)
        
        if to_be:
            gaps = Gap.query.filter_by(
                target_plateau_id=to_be.id,
                gap_kind=GAP_KIND_PLATEAU_TRANSITION
            ).order_by(Gap.created_at.desc()).all()
        else:
            gaps = []
            
        return (
            render_template(
                "interface_register/comparison.html",
                initiative=initiative,
                as_is=as_is,
                to_be=to_be,
                interfaces=interfaces,
                gaps=gaps,
                gap_types=interface_gap_service.GAP_TYPES,
                errors=str(exc),
            ),
            400,
        )


@interface_register_bp.route("/gaps/<int:gap_id>/work-packages", methods=["POST"])
@login_required
def attach_work_package(gap_id):
    """Attach a costed WorkPackage to an interface gap (US-6 AC6)."""
    guard = _guard()
    if guard:
        return guard

    initiative_id = request.form.get("initiative_id", type=int)
    try:
        service.resolve_initiative(initiative_id, g.current_org_id)
    except service.InterfaceRegisterError:
        return render_plain_error(404)

    try:
        work_package_service.attach_work_package_to_gap(
            gap_id,
            initiative_id=initiative_id,
            name=request.form.get("name"),
            estimated_cost=request.form.get("estimated_cost"),
            estimated_effort_hours=request.form.get("estimated_effort_hours"),
        )
        flash("Work package attached.", "success")
    except service.InterfaceRegisterError as exc:
        flash(str(exc), "error")

    return redirect(url_for("interface_register.comparison", initiative_id=initiative_id))
