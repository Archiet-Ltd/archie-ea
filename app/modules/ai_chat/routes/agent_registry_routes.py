"""R1-B56: Agent Registry routes -- register, see what activation still
needs, activate, and request a charter change (reviewed through R1-B07's
approval inbox, not a second screen)."""

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.modules.ai_chat.services import agent_registry_service as svc
from app.modules.ai_chat.services.agent_registry_service import (
    CharterChangeRefused,
    CrossOrganisationOwner,
    InvalidDelegatedLimit,
)
from app.utils.role_access import can_access_section

agent_registry_bp = Blueprint("agent_registry", __name__, url_prefix="/admin/agent-registry")


def _guard():
    if not can_access_section(current_user, "administration"):
        return render_template("errors/403.html"), 403
    return None


@agent_registry_bp.route("/")
@login_required
def index():
    guard = _guard()
    if guard:
        return guard
    return render_template(
        "ai_chat/agent_registry/list.html",
        registrations=svc.list_registrations(g.current_org_id),
    )


@agent_registry_bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    guard = _guard()
    if guard:
        return guard
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Name is required.", "error")
            return render_template("ai_chat/agent_registry/new.html")
        reg = svc.create_registration(
            organization_id=g.current_org_id,
            name=name,
            purpose=request.form.get("purpose", ""),
        )
        return redirect(url_for("agent_registry.detail", registration_id=reg.id))
    return render_template("ai_chat/agent_registry/new.html")


@agent_registry_bp.route("/<int:registration_id>")
@login_required
def detail(registration_id):
    guard = _guard()
    if guard:
        return guard
    reg = svc.get_registration(g.current_org_id, registration_id)
    if reg is None:
        return render_template("errors/404.html"), 404
    return render_template("ai_chat/agent_registry/detail.html", reg=reg)


@agent_registry_bp.route("/<int:registration_id>/owner", methods=["POST"])
@login_required
def set_owner(registration_id):
    guard = _guard()
    if guard:
        return guard
    reg = svc.get_registration(g.current_org_id, registration_id)
    if reg is None:
        return render_template("errors/404.html"), 404
    owner_user_id = request.form.get("owner_user_id", type=int)
    if owner_user_id:
        try:
            svc.set_owner(reg, owner_user_id)
            flash("Owner set.", "success")
        except CrossOrganisationOwner as exc:
            flash(str(exc), "error")
    return redirect(url_for("agent_registry.detail", registration_id=registration_id))


@agent_registry_bp.route("/<int:registration_id>/limits", methods=["POST"])
@login_required
def set_limits(registration_id):
    guard = _guard()
    if guard:
        return guard
    reg = svc.get_registration(g.current_org_id, registration_id)
    if reg is None:
        return render_template("errors/404.html"), 404
    max_writes = request.form.get("max_writes_per_day", type=int)
    if max_writes is not None:
        try:
            svc.set_delegated_limits(reg, {"max_writes_per_day": max_writes})
            flash("Delegated limits set.", "success")
        except InvalidDelegatedLimit as exc:
            flash(str(exc), "error")
    return redirect(url_for("agent_registry.detail", registration_id=registration_id))


@agent_registry_bp.route("/<int:registration_id>/charter", methods=["POST"])
@login_required
def propose_charter(registration_id):
    guard = _guard()
    if guard:
        return guard
    reg = svc.get_registration(g.current_org_id, registration_id)
    if reg is None:
        return render_template("errors/404.html"), 404
    persona = (request.form.get("persona") or "").strip()
    if not persona:
        flash("Persona is required.", "error")
        return redirect(url_for("agent_registry.detail", registration_id=registration_id))
    proposable = [a.strip() for a in request.form.get("proposable_actions", "").split(",") if a.strip()]
    forbidden = [a.strip() for a in request.form.get("forbidden_actions", "").split(",") if a.strip()]
    try:
        svc.request_charter_change(
            reg,
            persona=persona,
            purpose=request.form.get("purpose", ""),
            readable_entities=[],
            proposable_actions=proposable,
            forbidden_actions=forbidden,
            charter_text=request.form.get("charter_text", ""),
            requested_by_user_id=current_user.id,
        )
        flash("Charter change submitted for review in the approval inbox.", "success")
    except CharterChangeRefused as exc:
        flash(str(exc), "error")
    return redirect(url_for("agent_registry.detail", registration_id=registration_id))


@agent_registry_bp.route("/<int:registration_id>/activate", methods=["POST"])
@login_required
def activate(registration_id):
    guard = _guard()
    if guard:
        return guard
    reg = svc.get_registration(g.current_org_id, registration_id)
    if reg is None:
        return render_template("errors/404.html"), 404
    ok, missing = svc.activate(reg)
    if ok:
        flash("Agent activated.", "success")
    else:
        flash("Activation refused -- missing: " + ", ".join(missing), "error")
    return redirect(url_for("agent_registry.detail", registration_id=registration_id))
