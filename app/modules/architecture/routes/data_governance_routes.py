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
from app.modules.architecture.services.data_architecture_service import DataArchitectureService
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
        ("Sharing agreements", url_for("data_governance.sharing_agreements"), active == "agreements"),
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


@data_governance_bp.route("/lineage/<int:element_id>")
@login_required
def lineage_view(element_id):
    """R1-B80: multi-hop lineage held from source through every
    transformation, with the owner at each hop."""
    guard = _guard()
    if guard:
        return guard
    result = DataArchitectureService.multi_hop_lineage(element_id, g.current_org_id, max_hops=5)
    if not result["hops"]:
        return render_template("errors/404.html"), 404
    return render_template(
        "data_governance/lineage_view.html",
        hops=result["hops"],
        truncated=result["truncated"],
        start_element_id=element_id,
        tabs=_tabs("lineage"),
    )


@data_governance_bp.route("/sharing-agreements")
@login_required
def sharing_agreements():
    """R1-B80: the data-sharing agreement register (PB-0355)."""
    guard = _guard()
    if guard:
        return guard
    from app.models.data_sharing_agreement import DataSharingAgreement

    rows = (
        DataSharingAgreement.query.filter(DataSharingAgreement.organization_id == g.current_org_id)
        .order_by(DataSharingAgreement.name)
        .all()
    )
    return render_template(
        "data_governance/sharing_agreements.html", agreements=rows, tabs=_tabs("agreements"),
    )


@data_governance_bp.route("/sharing-agreements/new", methods=["GET", "POST"])
@login_required
def new_sharing_agreement():
    """R1-B80: register a new data-sharing agreement, linked to flows and
    a vendor."""
    guard = _guard()
    if guard:
        return guard
    from app.models.all_missing_models import DataLineage
    from app.models.data_sharing_agreement import DataSharingAgreement
    from app.models.vendor.vendor_organization import VendorOrganization

    vendors = VendorOrganization.query.order_by(VendorOrganization.name).all()
    preselected_flow_id = request.args.get("flow_id", type=int)

    unagreed_ids = DataSharingAgreement.unagreed_flow_ids(g.current_org_id)
    unagreed_flows = []
    if unagreed_ids:
        flow_rows = DataLineage.query.filter(DataLineage.id.in_(unagreed_ids)).all()
        unagreed_flows = [
            {"id": f.id, "label": f"Flow #{f.id}: element {f.archimate_element_id} → {f.target_archimate_element_id}"}
            for f in flow_rows
        ]

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        vendor_organization_id = request.form.get("vendor_organization_id", type=int)
        if not name or not vendor_organization_id:
            flash("Name and vendor are required.", "error")
            return redirect(url_for("data_governance.new_sharing_agreement"))
        agreement = DataSharingAgreement(
            name=name,
            vendor_organization_id=vendor_organization_id,
            organization_id=g.current_org_id,
            description=(request.form.get("description") or "").strip() or None,
            transfer_basis=(request.form.get("transfer_basis") or "").strip() or None,
            created_by_id=current_user.id,
        )
        flow_ids = request.form.getlist("flow_ids", type=int)
        if flow_ids:
            agreement.flows = DataLineage.query.filter(
                DataLineage.id.in_(flow_ids),
                DataLineage.organization_id == g.current_org_id,
            ).all()
        db.session.add(agreement)
        db.session.commit()
        flash("Data sharing agreement registered.", "success")
        return redirect(url_for("data_governance.sharing_agreements"))

    return render_template(
        "data_governance/new_sharing_agreement.html",
        vendors=vendors,
        unagreed_flows=unagreed_flows,
        preselected_flow_id=preselected_flow_id,
        tabs=_tabs("agreements"),
    )


@data_governance_bp.route("/impact/<int:element_id>")
@login_required
def downstream_impact_view(element_id):
    """R1-B80 (PB-0116): assess downstream impact of a change to
    *element_id* -- every consumer, ranked by business criticality."""
    guard = _guard()
    if guard:
        return guard
    result = DataArchitectureService.downstream_impact(element_id, g.current_org_id, max_hops=5)
    if not result["consumers"] and not DataArchitectureService.multi_hop_lineage(
        element_id, g.current_org_id, max_hops=1
    )["hops"]:
        return render_template("errors/404.html"), 404
    return render_template(
        "data_governance/downstream_impact.html",
        consumers=result["consumers"],
        truncated=result["truncated"],
        start_element_id=element_id,
        tabs=_tabs("lineage"),
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
