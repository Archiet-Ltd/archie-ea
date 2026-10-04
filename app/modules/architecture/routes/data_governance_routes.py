"""Data governance screens for the data architect.

System of record per data entity, entities held by several applications with no
declared source, the master data domain register, and the standards check on a
logical data model. Gated by the same ``data_integration`` section predicate the
sidebar uses, so a sidebar link can never 403.
"""

import logging

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models.all_missing_models import LogicalDataModel
from app.modules.architecture.services import data_sor_service as sor
from app.modules.architecture.services.data_model_validation_service import (
    DATA_STANDARDS,
    DataModelValidationService,
)
from app.utils.role_access import can_access_section

logger = logging.getLogger(__name__)

data_governance_bp = Blueprint("data_governance", __name__, url_prefix="/data-governance")


def _guard():
    if not can_access_section(current_user, "data_integration"):
        return render_template("errors/403.html"), 403
    return None


def _tabs(active):
    return [
        ("System of record", url_for("data_governance.entities"), active == "entities"),
        ("Undeclared copies", url_for("data_governance.undeclared_copies"), active == "copies"),
        ("Master data domains", url_for("data_governance.domains"), active == "domains"),
        ("Standards check", url_for("data_governance.models"), active == "models"),
    ]


@data_governance_bp.route("/entities")
@login_required
def entities():
    guard = _guard()
    if guard:
        return guard
    return render_template(
        "data_governance/entities.html",
        rows=sor.list_entities(g.current_org_id),
        tabs=_tabs("entities"),
    )


@data_governance_bp.route("/entities/<int:entity_id>")
@login_required
def entity_detail(entity_id):
    guard = _guard()
    if guard:
        return guard
    entity = sor.get_entity(g.current_org_id, entity_id)
    if entity is None:
        return render_template("errors/404.html"), 404
    declared = sor.get_application(g.current_org_id, entity.system_of_record_application_id)
    return render_template(
        "data_governance/entity_detail.html",
        entity=entity,
        declared=declared,
        holders=sor.entity_holders(g.current_org_id, entity),
        tabs=_tabs("entities"),
    )


@data_governance_bp.route("/entities/<int:entity_id>/system-of-record", methods=["POST"])
@login_required
def declare_system_of_record(entity_id):
    guard = _guard()
    if guard:
        return guard
    try:
        sor.declare_system_of_record(
            g.current_org_id,
            entity_id,
            request.form.get("application_id", type=int),
            user_id=current_user.id,
        )
        flash("System of record declared.", "success")
    except sor.DataSorError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("data_governance.entity_detail", entity_id=entity_id))


@data_governance_bp.route("/undeclared-copies")
@login_required
def undeclared_copies():
    guard = _guard()
    if guard:
        return guard
    return render_template(
        "data_governance/undeclared_copies.html",
        rows=sor.undeclared_copies(g.current_org_id),
        tabs=_tabs("copies"),
    )


@data_governance_bp.route("/domains")
@login_required
def domains():
    guard = _guard()
    if guard:
        return guard
    return render_template(
        "data_governance/domains.html",
        rows=sor.master_domains(g.current_org_id),
        tabs=_tabs("domains"),
    )


@data_governance_bp.route("/domains/<int:domain_id>/golden-source", methods=["POST"])
@login_required
def set_golden_source(domain_id):
    guard = _guard()
    if guard:
        return guard
    try:
        sor.declare_golden_source(
            g.current_org_id, domain_id, request.form.get("application_id", type=int)
        )
        flash("Golden source saved.", "success")
    except sor.DataSorError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("data_governance.domains"))


@data_governance_bp.route("/models")
@login_required
def models():
    guard = _guard()
    if guard:
        return guard
    rows = (
        LogicalDataModel.query.filter(LogicalDataModel.organization_id == g.current_org_id)
        .order_by(LogicalDataModel.name)
        .all()
    )
    return render_template("data_governance/models.html", models=rows, tabs=_tabs("models"))


@data_governance_bp.route("/models/<int:model_id>/standards")
@login_required
def model_standards(model_id):
    guard = _guard()
    if guard:
        return guard
    model = LogicalDataModel.query.filter(
        LogicalDataModel.id == model_id, LogicalDataModel.organization_id == g.current_org_id
    ).first()
    if model is None:
        return render_template("errors/404.html"), 404
    result = DataModelValidationService().check_logical_model_standards(model, g.current_org_id)
    return render_template(
        "data_governance/model_standards.html",
        model=model,
        result=result,
        standards=DATA_STANDARDS,
        tabs=_tabs("models"),
    )
