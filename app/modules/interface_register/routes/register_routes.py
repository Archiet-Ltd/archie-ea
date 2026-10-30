"""Routes for the Interface Register module (SAP S/4HANA Interface Register, Task 02).

Blueprint: interface_register_bp, url_prefix="/interface-register".
"""

from __future__ import annotations

import logging

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from app.models.application_portfolio import ApplicationComponent
from app.modules.interface_register.services import interface_register_service as service
from app.utils.role_access import can_access_section
from app.utils.plain_errors import render_plain_error

logger = logging.getLogger(__name__)

interface_register_bp = Blueprint(
    "interface_register",
    __name__,
    url_prefix="/interface-register",
    template_folder="../templates",
)


def _guard():
    """Same predicate the sidebar uses (data_integration section) — not a
    bespoke decorator, so a sidebar link can never 403 (root CLAUDE.md
    F-01/F-11/F-04 defect family)."""
    from flask_login import current_user

    if not can_access_section(current_user, "data_integration"):
        return render_plain_error(403)
    return None


def _resolve_current_provider_consumer(element):
    """D5 fix, factored for N2: resolve an interface's current provider/
    consumer so any re-render of the edit form (GET, or the POST error path)
    rehydrates the picker instead of showing an empty picker that implies
    "no provider" when one genuinely exists.

    N1 fix (round 5): this used to run its own unqualified
    ArchiMateRelationship.query.filter_by(...).first() lookups, which could
    resolve to a composition/serving edge this module never created (see
    interface_register_service.resolve_current_provider_relationship's
    docstring). Delegate to the service's ownership-aware resolution instead
    of duplicating the query here, so the edit-form picker and the
    compare-and-maybe-delete logic in update_interface() can never drift
    apart."""
    current_provider = None
    current_consumer = None

    provider_rel = service.resolve_current_provider_relationship(element)
    if provider_rel:
        provider_component = ApplicationComponent.query.filter_by(
            archimate_element_id=provider_rel.source_id
        ).first()
        if provider_component:
            current_provider = {"id": provider_component.id, "name": provider_component.name}

    consumer_rel = service.resolve_current_consumer_relationship(element)
    if consumer_rel:
        consumer_component = ApplicationComponent.query.filter_by(
            archimate_element_id=consumer_rel.target_id
        ).first()
        if consumer_component:
            current_consumer = {"id": consumer_component.id, "name": consumer_component.name}

    return current_provider, current_consumer


@interface_register_bp.route("/", methods=["GET"])
@login_required
def index():
    """US-1: register list. Redirects to the initiative picker when
    initiative_id is absent — never guesses one."""
    guard = _guard()
    if guard:
        return guard

    initiative_id = request.args.get("initiative_id", type=int)
    if not initiative_id:
        initiatives = service.list_initiatives_for_org(g.current_org_id)
        return render_template(
            "interface_register/index.html",
            initiative=None,
            initiatives=initiatives,
            rows=[],
        )

    try:
        initiative = service.resolve_initiative(initiative_id, g.current_org_id)
    except service.InterfaceRegisterError:
        return render_plain_error(404)

    rows = service.list_interfaces(initiative_id, g.current_org_id)
    return render_template(
        "interface_register/index.html",
        initiative=initiative,
        initiatives=None,
        rows=rows,
    )


@interface_register_bp.route("/new", methods=["GET"])
@login_required
def new():
    """US-2: create form."""
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

    return render_template(
        "interface_register/form.html",
        initiative=initiative,
        interface=None,
        metadata=None,
        errors=None,
        form_data=None,
    )


@interface_register_bp.route("/", methods=["POST"])
@login_required
def create():
    """US-2: create. Redirect-after-POST on success; re-render with an
    inline 4xx error on rejection — never a 500, never a silently dropped
    field."""
    guard = _guard()
    if guard:
        return guard

    initiative_id = request.form.get("initiative_id", type=int)
    if not initiative_id:
        flash("An initiative is required to create an interface.", "error")
        return redirect(url_for("interface_register.index"))

    try:
        initiative = service.resolve_initiative(initiative_id, g.current_org_id)
    except service.InterfaceRegisterError:
        return render_plain_error(404)

    try:
        element = service.create_interface(initiative_id, request.form, g.current_org_id)
    except service.InterfaceRegisterError as exc:
        return (
            render_template(
                "interface_register/form.html",
                initiative=initiative,
                interface=None,
                metadata=None,
                errors=str(exc),
                form_data=request.form,
            ),
            400,
        )

    flash(f'"{element.name}" added to the interface register.', "success")
    return redirect(url_for("interface_register.index", initiative_id=initiative_id))


@interface_register_bp.route("/<int:element_id>/edit", methods=["GET"])
@login_required
def edit(element_id):
    """US-3: edit form."""
    guard = _guard()
    if guard:
        return guard

    record = service.get_interface(element_id, g.current_org_id)
    if record is None:
        return render_plain_error(404)

    element = record["element"]
    props = element.custom_properties or {}
    initiative_id = props.get("initiative_id")
    initiative = None
    if initiative_id:
        try:
            initiative = service.resolve_initiative(initiative_id, g.current_org_id)
        except service.InterfaceRegisterError:
            initiative = None

    # D5 fix: resolve the interface's current provider/consumer so the edit
    # form's picker rehydrates instead of showing an empty picker that, if
    # saved without re-picking, silently leaves the old relationship
    # untouched while implying "no provider".
    current_provider, current_consumer = _resolve_current_provider_consumer(element)

    return render_template(
        "interface_register/form.html",
        initiative=initiative,
        interface=element,
        metadata=record["metadata"],
        errors=None,
        form_data=None,
        current_provider=current_provider,
        current_consumer=current_consumer,
    )


@interface_register_bp.route("/<int:element_id>", methods=["POST"])
@login_required
def update(element_id):
    """US-3: update, including retire."""
    guard = _guard()
    if guard:
        return guard

    record = service.get_interface(element_id, g.current_org_id)
    if record is None:
        return render_plain_error(404)

    props = record["element"].custom_properties or {}
    initiative_id = props.get("initiative_id")

    try:
        element = service.update_interface(element_id, request.form, g.current_org_id)
    except service.InterfaceRegisterError as exc:
        initiative = None
        if initiative_id:
            try:
                initiative = service.resolve_initiative(initiative_id, g.current_org_id)
            except service.InterfaceRegisterError:
                initiative = None

        # N2 fix: the 400 re-render also needs the current provider/consumer,
        # same as the GET edit path, or the picker misleadingly shows "no
        # provider" when the interface genuinely has one.
        current_provider, current_consumer = _resolve_current_provider_consumer(
            record["element"]
        )
        return (
            render_template(
                "interface_register/form.html",
                initiative=initiative,
                interface=record["element"],
                metadata=record["metadata"],
                errors=str(exc),
                form_data=request.form,
                current_provider=current_provider,
                current_consumer=current_consumer,
            ),
            400,
        )

    flash(f'"{element.name}" updated.', "success")
    return redirect(url_for("interface_register.index", initiative_id=initiative_id))
