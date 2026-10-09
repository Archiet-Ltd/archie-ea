"""Data governance screens for the data architect.

System of record per data entity, entities held by several applications with no
declared source, the master data domain register, and the standards check on a
logical data model. Gated by the same ``data_integration`` section predicate the
sidebar uses, so a sidebar link can never 403.

R1-B81 slice 2: steward assignment, no-steward list, classification proposal.
"""

import json
import logging

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
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
        ("No steward", url_for("data_governance.no_steward"), active == "no_steward"),
        ("Retention breaches", url_for("data_governance.retention_breaches"), active == "retention"),
        ("Data issues", url_for("data_governance.data_issues"), active == "issues"),
        ("Glossary", url_for("data_governance.glossary"), active == "glossary"),
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

    from app.models.application_owner import ApplicationOwner

    stewards = ApplicationOwner.get_display_rows_for_element(
        "data_entity", entity_id, g.current_org_id,
    )
    steward_owners = [s for s in stewards if s["ownership_type"] == "steward"]

    from app.models.user import User

    org_users = (
        User.query.filter_by(organization_id=g.current_org_id)
        .order_by(User.first_name, User.last_name)
        .all()
    )

    return render_template(
        "data_governance/entity_detail.html",
        entity=entity,
        declared=declared,
        holders=sor.entity_holders(g.current_org_id, entity),
        stewards=steward_owners,
        org_users=org_users,
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


# ------------------------------------------------------------------ #
# Steward assignment (R1-B81 slice 2)
# ------------------------------------------------------------------ #


@data_governance_bp.route("/entities/<int:entity_id>/steward", methods=["POST"])
@login_required
def assign_steward(entity_id):
    """Assign a data steward to a critical data entity through the one
    ownership writer (ApplicationOwner, element_type='data_entity',
    ownership_type='steward')."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    user_id = request.form.get("user_id", type=int)
    if not user_id:
        flash("A user must be selected.", "error")
        return redirect(url_for("data_governance.entity_detail", entity_id=entity_id))
    try:
        DataStewardshipService.set_data_entity_steward(
            entity_id=entity_id, user_id=user_id,
            organization_id=g.current_org_id, assigned_by=current_user.id,
        )
        db.session.commit()
        flash("Steward assigned.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("data_governance.entity_detail", entity_id=entity_id))


@data_governance_bp.route("/entities/<int:entity_id>/steward/<int:owner_id>/remove", methods=["POST"])
@login_required
def remove_steward(entity_id, owner_id):
    """Remove a steward from a data entity."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    DataStewardshipService.remove_data_entity_steward(
        owner_record_id=owner_id, organization_id=g.current_org_id,
    )
    db.session.commit()
    flash("Steward removed.", "success")
    return redirect(url_for("data_governance.entity_detail", entity_id=entity_id))


@data_governance_bp.route("/no-steward")
@login_required
def no_steward():
    """R1-B81 (PB-0317): critical data entities with no steward assigned."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    entities = DataStewardshipService.list_critical_entities_with_no_steward(g.current_org_id)
    return render_template(
        "data_governance/no_steward.html", entities=entities, tabs=_tabs("no_steward"),
    )


# ------------------------------------------------------------------ #
# Classification proposal (R1-B81 slice 2)
# ------------------------------------------------------------------ #


@data_governance_bp.route("/entities/<int:entity_id>/classify", methods=["POST"])
@login_required
def propose_classification(entity_id):
    """Propose a classification label for a data entity. Creates an
    approval row that another user can accept from the approval inbox."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    label = (request.form.get("classification_label") or "").strip().lower()
    if not label:
        flash("A classification label is required.", "error")
        return redirect(url_for("data_governance.entity_detail", entity_id=entity_id))
    try:
        result = DataStewardshipService.propose_classification(
            entity_id=entity_id, classification_label=label,
            organization_id=g.current_org_id, proposed_by=current_user.id,
        )
        if result.get("success"):
            flash(
                f"Classification proposal created (approval #{result['approval_id']}). "
                f"Another user can accept it from the approval inbox.",
                "success",
            )
        else:
            flash(result.get("error", "Failed to create proposal."), "error")
    except ValueError as exc:
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


@data_governance_bp.route("/retention-breaches")
@login_required
def retention_breaches():
    """R1-B81 (PB-0236): retention-policy breaches, with an owner or
    'not recorded' -- never a fabricated pass/fail."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    breaches = DataStewardshipService.retention_breaches(g.current_org_id)
    return render_template(
        "data_governance/retention_breaches.html", breaches=breaches, tabs=_tabs("retention"),
    )


@data_governance_bp.route("/issues")
@login_required
def data_issues():
    """R1-B81 (PB-0292): the data-issue list, routed-to shown from the
    entity's domain's recorded steward (legacy display, read-only)."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    issues = DataStewardshipService.list_issues(g.current_org_id)
    return render_template("data_governance/data_issues.html", issues=issues, tabs=_tabs("issues"))


@data_governance_bp.route("/issues/new", methods=["GET", "POST"])
@login_required
def new_data_issue():
    """R1-B81: raise a data issue against an entity."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    data_entity_id = request.args.get("data_entity_id", type=int) or request.form.get(
        "data_entity_id", type=int
    )

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title or not data_entity_id:
            flash("Title and entity are required.", "error")
            return redirect(url_for("data_governance.new_data_issue", data_entity_id=data_entity_id))
        try:
            DataStewardshipService.raise_issue(
                g.current_org_id, data_entity_id, title,
                (request.form.get("description") or "").strip() or None,
                current_user.id,
            )
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("data_governance.new_data_issue"))
        flash("Data issue raised.", "success")
        return redirect(url_for("data_governance.data_issues"))

    return render_template(
        "data_governance/new_data_issue.html", data_entity_id=data_entity_id, tabs=_tabs("issues"),
    )


@data_governance_bp.route("/issues/<int:issue_id>/resolve", methods=["POST"])
@login_required
def resolve_data_issue(issue_id):
    """R1-B81: resolve a data issue with a recorded fix."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    notes = (request.form.get("resolution_notes") or "").strip()
    try:
        DataStewardshipService.resolve_issue(g.current_org_id, issue_id, notes, current_user.id)
        db.session.commit()
        flash("Data issue resolved.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("data_governance.data_issues"))


@data_governance_bp.route("/glossary")
@login_required
def glossary():
    """R1-B81 (PB-0500): one definition per term."""
    guard = _guard()
    if guard:
        return guard
    from app.modules.architecture.services.data_stewardship_service import DataStewardshipService

    terms = DataStewardshipService.glossary_terms(g.current_org_id)
    return render_template("data_governance/glossary.html", terms=terms, tabs=_tabs("glossary"))
