"""Element properties: an organisation's governed metamodel definitions.

  GET  /metamodel/properties          the organisation's definitions, how many
                                      elements are missing each mandatory one,
                                      and (for those who may) the form to add one
  POST /metamodel/properties          save a definition
  GET  /metamodel/properties/<id>     one definition and every element of its
                                      type that has no value for it

A definition is an ``AcmPropertyTemplate`` row carrying the organisation (see
``GovernedPropertyService`` in ``property_service.py``), so the journey's
property panels, completeness scoring and default filling read it through the
one template reader. Values are checked against it by the element and proposal
property writers.

Reads are open to every signed-in member of the organisation; saving a
definition is limited to the roles that govern the metamodel. CSRF is covered
by the global ``CSRFProtect``; the form carries the token.
"""

from __future__ import annotations

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import role_required
from app.modules.architecture_assistant.property_service import (
    GOVERNED_PROPERTY_TYPES,
    GOVERNED_TYPE_LABELS,
    GovernedPropertyService,
    PropertyDefinitionError,
    metamodel_element_types,
)

# Who may change the organisation's metamodel. ``role_required`` also lets an
# administrator through, as every other role gate in the product does.
DEFINING_ROLES = ("enterprise_architect", "data_architect", "platform_admin")

metamodel_properties_bp = Blueprint(
    "metamodel_properties",
    __name__,
    url_prefix="/metamodel/properties",
    template_folder="../templates",
)


def _organization_id() -> int | None:
    org_id = getattr(g, "current_org_id", None)
    if org_id is not None:
        return int(org_id)
    org_id = getattr(current_user, "organization_id", None)
    return int(org_id) if org_id is not None else None


def _can_define() -> bool:
    if not getattr(current_user, "is_authenticated", False):
        return False
    try:
        if current_user.is_admin():
            return True
    except Exception:
        current_app.logger.debug("is_admin unavailable for metamodel role check")
    return getattr(current_user, "enterprise_role", None) in DEFINING_ROLES


def _render_index(form=None, error=None, status=200):
    org_id = _organization_id()
    service = GovernedPropertyService()
    definitions = service.definitions(org_id) if org_id is not None else []
    rows = []
    for definition in definitions:
        missing = service.missing_values(definition, org_id) if definition.is_mandatory else None
        rows.append({"definition": definition, "missing_count": None if missing is None else len(missing)})
    return (
        render_template(
            "metamodel/properties.html",
            rows=rows,
            can_define=_can_define(),
            element_types=metamodel_element_types(),
            value_types=[(t, GOVERNED_TYPE_LABELS[t]) for t in GOVERNED_PROPERTY_TYPES],
            type_labels=GOVERNED_TYPE_LABELS,
            form=form or {},
            error=error,
        ),
        status,
    )


@metamodel_properties_bp.route("", methods=["GET"])
@login_required
def index():
    return _render_index()


@metamodel_properties_bp.route("", methods=["POST"])
@login_required
@role_required(*DEFINING_ROLES)
def define():
    form = {
        "archimate_type": request.form.get("archimate_type", ""),
        "display_name": request.form.get("display_name", ""),
        "property_type": request.form.get("property_type", ""),
        "allowed_values": request.form.get("allowed_values", ""),
        "mandatory": request.form.get("mandatory") == "on",
        "help_text": request.form.get("help_text", ""),
    }
    allowed = [line for line in form["allowed_values"].replace(",", "\n").splitlines() if line.strip()]
    try:
        definition = GovernedPropertyService().define(
            _organization_id(),
            archimate_type=form["archimate_type"],
            display_name=form["display_name"],
            property_type=form["property_type"],
            allowed_values=allowed,
            mandatory=form["mandatory"],
            help_text=form["help_text"],
        )
    except PropertyDefinitionError as exc:
        return _render_index(form=form, error=str(exc), status=400)
    return redirect(url_for("metamodel_properties.detail", definition_id=definition.id))


@metamodel_properties_bp.route("/<int:definition_id>", methods=["GET"])
@login_required
def detail(definition_id: int):
    org_id = _organization_id()
    service = GovernedPropertyService()
    definition = service.definition(org_id, definition_id) if org_id is not None else None
    if definition is None:
        abort(404)
    missing = service.missing_values(definition, org_id)
    return render_template(
        "metamodel/property_detail.html",
        definition=definition,
        missing=missing,
        type_labels=GOVERNED_TYPE_LABELS,
    )


__all__ = ["metamodel_properties_bp", "DEFINING_ROLES"]
