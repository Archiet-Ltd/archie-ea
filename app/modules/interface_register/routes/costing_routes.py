"""Read-only S/4HANA programme costing rollup. Blueprint: interface_register_bp (registered elsewhere), route /interface-register/costing."""

from __future__ import annotations

from flask import redirect, render_template, request, url_for
from flask_login import login_required

from app.modules.interface_register.routes.register_routes import interface_register_bp, _guard
from app.modules.interface_register.services import interface_register_service as service, programme_rollup_service
from app.utils.plain_errors import render_plain_error


@interface_register_bp.route("/costing", methods=["GET"])
@login_required
def costing():
    """Display the programme costing rollup view."""
    guard = _guard()
    if guard:
        return guard

    initiative_id = request.args.get("initiative_id", type=int)
    if not initiative_id:
        return redirect(url_for("interface_register.index"))

    try:
        rollup = programme_rollup_service.interface_programme_rollup(initiative_id)
    except service.InterfaceRegisterError:
        return render_plain_error(404)

    return render_template(
        "interface_register/costing.html",
        rollup=rollup,
        initiative=rollup["initiative"]
    )
