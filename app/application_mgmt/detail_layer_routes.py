"""
Detail layer/vendor/capability/document routes for Application Management.
"""
# mass-deletion-ok — BE-179 removes 16 manual CSRF blocks replaced by global CSRFProtect

import json
import logging
from datetime import datetime

from flask import current_app, flash, jsonify, request, session
from flask_login import login_required
from sqlalchemy import func, select

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.archimate_business import (
    BusinessCollaboration,
    BusinessInteraction,
    BusinessInterface,
    Contract,
    Representation,
)
from ..models.archimate_technology import (
    Resource,
    TechnologyCollaborationFull,
    TechnologyEvent,
    TechnologyFunction,
    TechnologyInteraction,
    TechnologyProcess,
)
from ..models.business_capabilities import BusinessCapability
from ..models.business_layer import (
    BusinessEvent,
)
from ..models.motivation import Assessment, Meaning, Stakeholder, Value
from ..models.technology_layer import (
    CommunicationNetwork,
    Device,
    Node,
    Path,
    SystemSoftware,
    TechnologyInterface,
    TechnologyService,
)
from ..models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from ..models.unified_capability import UnifiedCapability
from ..models.vendor.vendor_organization import VendorProduct, application_vendor_products
from . import application_mgmt
from .forms import (
    ApplicationLayerForm,
    BusinessLayerForm,
    MotivationLayerForm,
    StrategyLayerForm,
    TechnologyLayerForm,
)
from .routes import (
    CAPABILITY_SUPPORT_LEVEL_CHOICES,
    VENDOR_CRITICALITY_CHOICES,
    VENDOR_DEPLOYMENT_CHOICES,
    VENDOR_HOSTING_CHOICES,
    _redirect_to_detail,
)

logger = logging.getLogger(__name__)


@application_mgmt.route(
    "/applications/<int:id>/documents/<int:doc_id>/update", methods=["POST"]
)
@login_required
def update_document_file(id, doc_id):
    """Update document metadata."""
    app = ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

    try:
        # Find the document (assuming there's a document model)
        from app.models.application_layer import ApplicationDocument

        # tenant-scoping-ok: doc_id alone is not org-scoped (ApplicationDocument
        # has no TenantMixin) -- anchor to the org-scoped `app` loaded above to
        # close a cross-org document-update IDOR.
        doc = ApplicationDocument.query.filter_by(
            id=doc_id, application_component_id=app.id
        ).first_or_404()

        # Update document fields
        if request.form.get("description"):
            doc.description = request.form.get("description")

        db.session.commit()
        flash("Document updated successfully!", "success")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating document: {str(e)}")
        flash("Error updating document. Please try again.", "error")

    return _redirect_to_detail(app.id, tab="dependencies")


@application_mgmt.route(
    "/applications/<int:id>/capability-mapping/<int:mapping_id>/update",
    methods=["POST"],
)
@login_required
def update_capability_mapping(id, mapping_id):
    """Update capability mapping."""
    app = ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

    try:
        # UnifiedApplicationCapabilityMapping is imported at module level (line ~45).
        # A local re-import here used to shadow it with a wrong path
        # (app.models.application_layer, which does not define this class), so the update
        # always hit the except branch below and never persisted -- caught while adding the
        # tenant-ownership check just below.
        mapping = UnifiedApplicationCapabilityMapping.query.get_or_404(mapping_id)

        if mapping.application_component_id != app.id:
            flash("That capability mapping does not belong to this application.", "error")
            return _redirect_to_detail(app.id, tab="capabilities")

        # Update mapping fields
        if request.form.get("support_level"):
            mapping.support_level = request.form.get("support_level")
        if request.form.get("coverage_percentage"):
            mapping.coverage_percentage = float(request.form.get("coverage_percentage"))
        if request.form.get("maturity_level"):
            mapping.maturity_level = request.form.get("maturity_level")
        mapping.is_strategic = "is_strategic" in request.form
        if request.form.get("notes"):
            mapping.notes = request.form.get("notes")

        db.session.commit()
        flash("Capability mapping updated successfully!", "success")

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating capability mapping: {str(e)}")
        flash("Error updating capability mapping. Please try again.", "error")

    return _redirect_to_detail(app.id, tab="capabilities")


@application_mgmt.route("/applications/<int:id>/vendor-footprint", methods=["POST"])
@login_required
def application_vendor_footprint(id):
    """Create, update, or remove vendor product mappings for an application."""
    app = ApplicationComponent.query.get_or_404(id)

    if not app.archimate_element_id:
        flash(
            "Link this application to an ArchiMate element before managing vendor footprint.",
            "error",
        )
        return _redirect_to_detail(app.id, tab="vendors")

    action = request.form.get("action", "add").strip().lower()

    def _normalize_choice(raw_value, valid_choices):
        if not raw_value:
            return None
        normalized = raw_value.strip()
        valid_values = {choice[0] for choice in valid_choices}
        if normalized not in valid_values:
            raise ValueError(f"Invalid option: {normalized}")
        return normalized

    def _parse_optional_date(raw_value):
        if not raw_value:
            return None
        raw_value = raw_value.strip()
        if not raw_value:
            return None
        for pattern in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw_value, pattern)
            except ValueError:
                continue
        raise ValueError(f"Invalid date format: {raw_value}")

    try:
        vendor_product_id = int(request.form.get("vendor_product_id", "0"))
    except (TypeError, ValueError):
        vendor_product_id = None

    if not vendor_product_id:
        flash("Select a vendor product before submitting.", "error")
        return _redirect_to_detail(app.id, tab="vendors")

    product = None
    if action != "delete":
        try:
            product = db.session.get(VendorProduct, vendor_product_id)
        except Exception:
            product = None

        if not product:
            flash("Vendor product not found.", "error")
            return _redirect_to_detail(app.id, tab="vendors")

    table = application_vendor_products

    try:
        if action == "delete":
            db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
                table.delete().where(
                    table.c.archimate_element_id == app.archimate_element_id,
                    table.c.vendor_product_id == vendor_product_id,
                )
            )
            db.session.commit()

            remaining = db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id)
                select(func.count())
                .select_from(table)
                .where(table.c.archimate_element_id == app.archimate_element_id)
            ).scalar()

            if remaining == 0 and (app.deployment_status or "").lower() == "production":
                flash(
                    "Vendor footprint removed. Production applications should map at least one vendor product.",
                    "warning",
                )
            else:
                flash("Vendor product removed from footprint.", "success")

            return _redirect_to_detail(app.id, tab="vendors")

        deployment_type = _normalize_choice(
            request.form.get("deployment_type"), VENDOR_DEPLOYMENT_CHOICES
        )
        criticality = _normalize_choice(
            request.form.get("criticality"), VENDOR_CRITICALITY_CHOICES
        )
        hosting_model = _normalize_choice(
            request.form.get("hosting_model"), VENDOR_HOSTING_CHOICES
        )
        implementation_date = _parse_optional_date(
            request.form.get("implementation_date")
        )
        retirement_date = _parse_optional_date(request.form.get("retirement_date"))
        notes = request.form.get("notes", "").strip() or None

        existing = db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
            select(table).where(
                table.c.archimate_element_id == app.archimate_element_id,
                table.c.vendor_product_id == vendor_product_id,
            )
        ).first()

        payload = {
            "deployment_type": deployment_type,
            "criticality": criticality,
            "hosting_model": hosting_model,
            "implementation_date": implementation_date,
            "retirement_date": retirement_date,
            "notes": notes,
        }

        if existing:
            db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
                table.update()
                .where(
                    table.c.archimate_element_id == app.archimate_element_id,
                    table.c.vendor_product_id == vendor_product_id,
                )
                .values(**payload)
            )
            db.session.commit()
            flash("Vendor footprint updated.", "success")
        else:
            db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
                table.insert().values(
                    archimate_element_id=app.archimate_element_id,
                    vendor_product_id=vendor_product_id,
                    **payload,
                )
            )
            db.session.commit()
            flash("Vendor product linked to this application.", "success")

    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception as exc:
        db.session.rollback()
        flash(f"Unable to update vendor footprint: {exc}", "error")

    return _redirect_to_detail(app.id, tab="vendors")


@application_mgmt.route("/applications/<int:id>/capability-mappings", methods=["POST"])
@login_required
def application_capability_mapping_create(id):
    """Attach an enterprise capability to an application component."""
    app = ApplicationComponent.query.get_or_404(id)

    try:
        capability_id = int(request.form.get("capability_id", "0"))
    except (TypeError, ValueError):
        capability_id = 0

    if not capability_id:
        flash("Select a capability to link before submitting.", "error")
        return _redirect_to_detail(app.id, tab="capabilities")

    capability = db.session.get(BusinessCapability, capability_id)
    if not capability:
        flash("Selected capability was not found.", "error")
        return _redirect_to_detail(app.id, tab="capabilities")

    # The picker posts a BusinessCapability.id, but UnifiedApplicationCapabilityMapping
    # FKs to unified_capabilities.id — a different sequence (ADR-0008: unified_capabilities
    # is the canonical, provenance-tracked projection of business_capability, kept in sync
    # by `flask project-capabilities`). Resolve the real projected row rather than reusing
    # the BusinessCapability id directly, which only worked by accident where the two
    # sequences happened to collide and otherwise violated the FK.
    unified_capability = UnifiedCapability.query.filter_by(
        source_table="business_capability", source_id=str(capability_id)
    ).first()
    if not unified_capability:
        flash(
            f"{capability.name} has not finished syncing to the capability store yet — "
            "try again shortly, or ask an administrator to run the capability projection.",
            "error",
        )
        return _redirect_to_detail(app.id, tab="capabilities")

    existing = UnifiedApplicationCapabilityMapping.query.filter_by(
        application_component_id=app.id, unified_capability_id=unified_capability.id
    ).first()
    if existing:
        flash("Capability already linked to this application.", "info")
        return _redirect_to_detail(app.id, tab="capabilities")

    support_level = (request.form.get("support_level") or "").strip().lower()
    valid_support_levels = {choice[0] for choice in CAPABILITY_SUPPORT_LEVEL_CHOICES}
    if support_level and support_level not in valid_support_levels:
        flash("Select a valid support level.", "error")
        return _redirect_to_detail(app.id, tab="capabilities")

    coverage_raw = (request.form.get("coverage_percentage") or "").strip()
    coverage_value = None
    if coverage_raw:
        try:
            coverage_value = int(coverage_raw)
        except ValueError:
            flash("Coverage percentage must be an integer between 0 and 100.", "error")
            return _redirect_to_detail(app.id, tab="capabilities")
        if coverage_value < 0 or coverage_value > 100:
            flash("Coverage percentage must be between 0 and 100.", "error")
            return _redirect_to_detail(app.id, tab="capabilities")

    maturity_raw = (request.form.get("maturity_level") or "").strip()
    maturity_value = None
    if maturity_raw:
        try:
            maturity_value = int(maturity_raw)
        except ValueError:
            flash("Maturity level must be between 1 and 5.", "error")
            return _redirect_to_detail(app.id, tab="capabilities")
        if maturity_value < 1 or maturity_value > 5:
            flash("Maturity level must be between 1 and 5.", "error")
            return _redirect_to_detail(app.id, tab="capabilities")

    notes_value = (request.form.get("notes") or "").strip() or None
    is_strategic = request.form.get("is_strategic") in {"on", "true", "1"}

    mapping = UnifiedApplicationCapabilityMapping(
        application_component_id=app.id,
        unified_capability_id=unified_capability.id,
        support_level=support_level or None,
        coverage_percentage=coverage_value,
        maturity_level=maturity_value,
        is_strategic=is_strategic,
        notes=notes_value,
    )

    try:
        db.session.add(mapping)
        db.session.commit()
        flash(f"Linked {capability.name} to {app.name}.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to link capability %s to application %s", capability_id, app.id
        )
        # Never interpolate the raw exception into user-facing text — it leaked
        # internal table names, the full INSERT and its parameter dictionary.
        flash("Unable to link that capability. The team has been notified.", "error")

    return _redirect_to_detail(app.id, tab="capabilities")


@application_mgmt.route(
    "/applications/<int:id>/capability-mappings/<int:mapping_id>/delete",
    methods=["POST"],
)
@login_required
def application_capability_mapping_delete(id, mapping_id):
    """Detach a capability mapping from an application."""
    app = ApplicationComponent.query.get_or_404(id)
    mapping = UnifiedApplicationCapabilityMapping.query.get_or_404(mapping_id)

    if mapping.application_component_id != app.id:
        flash("That capability mapping does not belong to this application.", "error")
        return _redirect_to_detail(app.id, tab="capabilities")

    capability = db.session.get(BusinessCapability, mapping.unified_capability_id)
    capability_name = capability.name if capability else "Capability"

    try:
        db.session.delete(mapping)
        db.session.commit()
        flash(f"Removed {capability_name} from {app.name}.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Unable to remove capability mapping: {exc}", "error")

    return _redirect_to_detail(app.id, tab="capabilities")


# ============================================================================
# Layer Edit Routes - Option 1 + Option 2 Implementation
# ============================================================================
# These routes handle inline editing of ArchiMate layer elements with CSRF protection
# and populate_obj pattern using layer-specific WTForms


@application_mgmt.route(
    "/applications/<int:id>/layers/motivation-update", methods=["POST"]
)
@login_required
def update_motivation_layer(id):
    """Update Motivation Layer elements (Requirements, Stakeholders, Drivers, Goals)"""
    ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

    try:
        form = MotivationLayerForm(request.form)

        if not form.validate():

            def format_errors(errors_dict):
                formatted = []
                for field, msgs in errors_dict.items():
                    if isinstance(msgs, dict):
                        for sub_field, sub_msgs in msgs.items():
                            if isinstance(sub_msgs, list):
                                formatted.append(
                                    f"{field}.{sub_field}: {', '.join(str(m) for m in sub_msgs)}"
                                )
                            else:
                                formatted.append(f"{field}.{sub_field}: {sub_msgs}")
                    elif isinstance(msgs, list):
                        formatted.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        formatted.append(f"{field}: {msgs}")
                return formatted

            errors = format_errors(form.errors)
            return jsonify({"success": False, "errors": errors}), 400

        changed_fields = []

        # Process requirement updates and deletions
        if form.requirements.data:
            from ..models.models import Requirement

            for req_data in form.requirements.data:
                req_id = req_data.get("id")
                req_delete = req_data.get("_delete")

                # Handle deletion
                if req_id and req_delete:
                    req_obj = Requirement.query.get(int(req_id))
                    if req_obj:
                        db.session.delete(req_obj)
                        changed_fields.append(f"requirement_{req_id}_deleted")
                    continue

                # Handle update
                req_name = req_data.get("name")
                req_type = req_data.get("requirement_type")
                req_priority = req_data.get("priority")

                if req_id and req_name:
                    req_obj = Requirement.query.get(int(req_id))
                    if req_obj:
                        old_name = req_obj.title
                        req_obj.title = req_name
                        req_obj.type = req_type or "functional"
                        req_obj.priority = req_priority or "medium"

                        if old_name != req_name:
                            changed_fields.append(f"requirement_{req_id}")
                        db.session.add(req_obj)

        # Process stakeholder updates and deletions
        if form.stakeholders.data:
            for stakeholder_data in form.stakeholders.data:
                stakeholder_id = stakeholder_data.get("id")
                stakeholder_delete = stakeholder_data.get("_delete")
                if stakeholder_id and stakeholder_delete:
                    stakeholder_obj = Stakeholder.query.get(int(stakeholder_id))
                    if stakeholder_obj:
                        db.session.delete(stakeholder_obj)
                        changed_fields.append(f"stakeholder_{stakeholder_id}_deleted")
                    continue
                stakeholder_name = stakeholder_data.get("name")
                stakeholder_role = stakeholder_data.get("role")
                if stakeholder_id and stakeholder_name:
                    stakeholder_obj = Stakeholder.query.get(int(stakeholder_id))
                    if stakeholder_obj:
                        has_changed = False
                        if stakeholder_obj.name != stakeholder_name:
                            stakeholder_obj.name = stakeholder_name
                            has_changed = True
                        if hasattr(
                            stakeholder_obj, "role"
                        ) and stakeholder_obj.role != (stakeholder_role or ""):
                            stakeholder_obj.role = stakeholder_role or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"stakeholder_{stakeholder_id}")
                            db.session.add(stakeholder_obj)

        # Process driver updates and deletions
        if form.drivers.data:
            from ..models.motivation import Driver

            for driver_data in form.drivers.data:
                driver_id = driver_data.get("id")
                driver_delete = driver_data.get("_delete")
                if driver_id and driver_delete:
                    driver_obj = Driver.query.get(int(driver_id))
                    if driver_obj:
                        db.session.delete(driver_obj)
                        changed_fields.append(f"driver_{driver_id}_deleted")
                driver_name = driver_data.get("name")
                driver_type = driver_data.get("driver_type")
                if driver_id and driver_name:
                    driver_obj = Driver.query.get(int(driver_id))
                    if driver_obj:
                        has_changed = False
                        if driver_obj.name != driver_name:
                            driver_obj.name = driver_name
                            has_changed = True
                        if hasattr(
                            driver_obj, "driver_type"
                        ) and driver_obj.driver_type != (driver_type or ""):
                            driver_obj.driver_type = driver_type or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"driver_{driver_id}")
                            db.session.add(driver_obj)

        # Process goal updates and deletions
        if form.goals.data:
            from ..models.motivation import Goal

            for goal_data in form.goals.data:
                goal_id = goal_data.get("id")
                goal_delete = goal_data.get("_delete")
                if goal_id and goal_delete:
                    goal_obj = Goal.query.get(int(goal_id))
                    if goal_obj:
                        db.session.delete(goal_obj)
                        changed_fields.append(f"goal_{goal_id}_deleted")
                goal_name = goal_data.get("name")
                goal_priority = goal_data.get("priority")
                if goal_id and goal_name:
                    goal_obj = Goal.query.get(int(goal_id))
                    if goal_obj:
                        has_changed = False
                        if goal_obj.name != goal_name:
                            goal_obj.name = goal_name
                            has_changed = True
                        if hasattr(goal_obj, "priority") and goal_obj.priority != (
                            goal_priority or ""
                        ):
                            goal_obj.priority = goal_priority or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"goal_{goal_id}")
                            db.session.add(goal_obj)

        # Process assessment updates and deletions
        if form.assessments.data:
            for assessment_data in form.assessments.data:
                assessment_id = assessment_data.get("id")
                assessment_delete = assessment_data.get("_delete")
                if assessment_id and assessment_delete:
                    assessment_obj = Assessment.query.get(int(assessment_id))
                    if assessment_obj:
                        db.session.delete(assessment_obj)
                        changed_fields.append(f"assessment_{assessment_id}_deleted")
                assessment_name = assessment_data.get("name")
                assessment_description = assessment_data.get("description")
                if assessment_id and assessment_name:
                    assessment_obj = Assessment.query.get(int(assessment_id))
                    if assessment_obj:
                        has_changed = False
                        if assessment_obj.name != assessment_name:
                            assessment_obj.name = assessment_name
                            has_changed = True
                        if assessment_obj.description != (assessment_description or ""):
                            assessment_obj.description = assessment_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"assessment_{assessment_id}")
                            db.session.add(assessment_obj)

        # Process outcome updates and deletions
        if form.outcomes.data:
            from ..models.models import Outcome

            for outcome_data in form.outcomes.data:
                outcome_id = outcome_data.get("id")
                outcome_delete = outcome_data.get("_delete")
                if outcome_id and outcome_delete:
                    outcome_obj = Outcome.query.get(int(outcome_id))
                    if outcome_obj:
                        db.session.delete(outcome_obj)
                        changed_fields.append(f"outcome_{outcome_id}_deleted")
                outcome_name = outcome_data.get("name")
                outcome_description = outcome_data.get("description")
                if outcome_id and outcome_name:
                    outcome_obj = Outcome.query.get(int(outcome_id))
                    if outcome_obj:
                        has_changed = False
                        if outcome_obj.name != outcome_name:
                            outcome_obj.name = outcome_name
                            has_changed = True
                        if outcome_obj.description != (outcome_description or ""):
                            outcome_obj.description = outcome_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"outcome_{outcome_id}")
                            db.session.add(outcome_obj)

        # Process constraint updates and deletions.
        # The Constraint model does not exist yet, so this block is switched off at
        # `if False:` and is unreachable. The noqa markers below record that the
        # undefined name is a known consequence of that, not an unfixed defect —
        # remove them (and the `if False:`) when the model lands.
        if False:  # Disabled until Constraint model is created
            for constraint_data in form.constraints.data:
                constraint_id = constraint_data.get("id")
                constraint_delete = constraint_data.get("_delete")
                if constraint_id and constraint_delete:
                    constraint_obj = Constraint.query.get(int(constraint_id))  # noqa: F821 — model not yet created
                    if constraint_obj:
                        db.session.delete(constraint_obj)
                        changed_fields.append(f"constraint_{constraint_id}_deleted")
                constraint_name = constraint_data.get("name")
                constraint_description = constraint_data.get("description")
                if constraint_id and constraint_name:
                    constraint_obj = Constraint.query.get(int(constraint_id))  # noqa: F821 — model not yet created
                    if constraint_obj:
                        has_changed = False
                        if constraint_obj.name != constraint_name:
                            constraint_obj.name = constraint_name
                            has_changed = True
                        if constraint_obj.description != (constraint_description or ""):
                            constraint_obj.description = constraint_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"constraint_{constraint_id}")
                            db.session.add(constraint_obj)

        # Process value updates and deletions
        if form.values.data:
            for value_data in form.values.data:
                value_id = value_data.get("id")
                value_delete = value_data.get("_delete")
                if value_id and value_delete:
                    value_obj = Value.query.get(int(value_id))
                    if value_obj:
                        db.session.delete(value_obj)
                        changed_fields.append(f"value_{value_id}_deleted")

        # Process meaning updates and deletions
        if form.meanings.data:
            for meaning_data in form.meanings.data:
                meaning_id = meaning_data.get("id")
                meaning_delete = meaning_data.get("_delete")
                if meaning_id and meaning_delete:
                    meaning_obj = Meaning.query.get(int(meaning_id))
                    if meaning_obj:
                        db.session.delete(meaning_obj)
                        changed_fields.append(f"meaning_{meaning_id}_deleted")
                meaning_name = meaning_data.get("name")
                meaning_description = meaning_data.get("description")
                if meaning_id and meaning_name:
                    meaning_obj = Meaning.query.get(int(meaning_id))
                    if meaning_obj:
                        has_changed = False
                        if meaning_obj.name != meaning_name:
                            meaning_obj.name = meaning_name
                            has_changed = True
                        if meaning_obj.description != (meaning_description or ""):
                            meaning_obj.description = meaning_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"meaning_{meaning_id}")
                            db.session.add(meaning_obj)

        db.session.commit()

        # Store changed field IDs in session for highlighting
        if changed_fields:
            try:
                session["motivation_changes"] = json.dumps(changed_fields)
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug(f"Ignored: {e}")

        return jsonify(
            {
                "success": True,
                "message": f"Motivation layer updated ({len(changed_fields)} changes)",
                "changed": changed_fields,
            }
        )

    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error updating motivation layer: {str(exc)}")
        return jsonify({"success": False, "error": str(exc)}), 500


@application_mgmt.route(
    "/applications/<int:id>/layers/strategy-update", methods=["POST"]
)
@login_required
def update_strategy_layer(id):
    """Update Strategy Layer elements (Capabilities, Resources, Value Streams, Courses of Action)"""
    ApplicationComponent.query.get_or_404(id)

    # csrf-ok: global CSRFProtect active

    try:
        form = StrategyLayerForm(request.form)

        if not form.validate():
            # Handle nested form errors (FieldList with FormField)
            def format_errors(errors_dict):
                formatted = []
                for field, msgs in errors_dict.items():
                    if isinstance(msgs, dict):
                        # Nested form errors
                        for sub_field, sub_msgs in msgs.items():
                            if isinstance(sub_msgs, list):
                                formatted.append(
                                    f"{field}.{sub_field}: {', '.join(str(m) for m in sub_msgs)}"
                                )
                            else:
                                formatted.append(f"{field}.{sub_field}: {sub_msgs}")
                    elif isinstance(msgs, list):
                        # Regular field errors
                        formatted.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        formatted.append(f"{field}: {msgs}")
                return formatted

            errors = format_errors(form.errors)
            return jsonify({"success": False, "errors": errors}), 400

        changed_fields = []

        # Process capability updates and deletions
        if form.capabilities.data:
            from ..models.business_capabilities import Capability

            for cap_data in form.capabilities.data:
                cap_id = cap_data.get("id")
                cap_delete = cap_data.get("_delete")

                # Handle deletion
                if cap_id and cap_delete:
                    cap_obj = Capability.query.get(int(cap_id))
                    if cap_obj:
                        db.session.delete(cap_obj)
                        changed_fields.append(f"capability_{cap_id}_deleted")
                    continue

                # Handle update
                cap_name = cap_data.get("name")
                cap_level = cap_data.get("level")
                cap_maturity = cap_data.get("maturity")
                cap_coverage = cap_data.get("coverage")

                if cap_id and cap_name:
                    cap_obj = Capability.query.get(int(cap_id))
                    if cap_obj:
                        old_name = cap_obj.name
                        cap_obj.name = cap_name
                        cap_obj.level = cap_level or 1
                        cap_obj.maturity_level = cap_maturity or "initial"
                        cap_obj.coverage_percentage = cap_coverage or 0

                        if old_name != cap_name:
                            changed_fields.append(f"capability_{cap_id}")
                        db.session.add(cap_obj)

        # Process resource updates and deletions
        if form.resources.data:
            for res_data in form.resources.data:
                res_id = res_data.get("id")
                res_delete = res_data.get("_delete")

                # Handle deletion
                if res_id and res_delete:
                    res_obj = Resource.query.get(int(res_id))
                    if res_obj:
                        db.session.delete(res_obj)
                        changed_fields.append(f"resource_{res_id}_deleted")
                    continue

                # Handle update
                res_name = res_data.get("name")
                res_type = res_data.get("resource_type")
                res_avail = res_data.get("availability")

                if res_id and res_name:
                    res_obj = Resource.query.get(int(res_id))
                    if res_obj:
                        old_name = res_obj.name
                        res_obj.name = res_name
                        res_obj.resource_type = res_type or "technical"
                        if hasattr(res_obj, "availability"):
                            res_obj.availability = res_avail or "shared"

                        if old_name != res_name:
                            changed_fields.append(f"resource_{res_id}")
                        db.session.add(res_obj)

        db.session.commit()

        if changed_fields:
            try:
                session["strategy_changes"] = changed_fields
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug(f"Ignored: {e}")

        return jsonify(
            {
                "success": True,
                "message": f"Strategy layer updated ({len(changed_fields)} changes)",
                "changed": changed_fields,
            }
        )

    except Exception as exc:
        db.session.rollback()
        import traceback

        error_trace = traceback.format_exc()
        current_app.logger.error(
            f"Error updating strategy layer: {str(exc)}\n{error_trace}"
        )
        return jsonify({"success": False, "error": str(exc), "trace": error_trace}), 500


@application_mgmt.route(
    "/applications/<int:id>/layers/business-update", methods=["POST"]
)
@login_required
def update_business_layer(id):
    """Update Business Layer elements (Services, Processes, Actors, Roles)"""
    ApplicationComponent.query.get_or_404(id)
    # csrf-ok: global CSRFProtect active
    try:
        form = BusinessLayerForm(request.form)
        if not form.validate():

            def format_errors(errors_dict):
                formatted = []
                for field, msgs in errors_dict.items():
                    if isinstance(msgs, dict):
                        for sub_field, sub_msgs in msgs.items():
                            if isinstance(sub_msgs, list):
                                formatted.append(
                                    f"{field}.{sub_field}: {', '.join(str(m) for m in sub_msgs)}"
                                )
                            else:
                                formatted.append(f"{field}.{sub_field}: {sub_msgs}")
                    elif isinstance(msgs, list):
                        formatted.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        formatted.append(f"{field}: {msgs}")
                return formatted

            errors = format_errors(form.errors)
            return jsonify({"success": False, "errors": errors}), 400
        changed_fields = []
        # Process business service updates and deletions
        if form.services.data:
            from ..models.business_layer import BusinessService

            for svc_data in form.services.data:
                svc_id = svc_data.get("id")
                svc_delete = svc_data.get("_delete")
                if svc_id and svc_delete:
                    svc_obj = BusinessService.query.get(int(svc_id))
                    if svc_obj:
                        db.session.delete(svc_obj)
                        changed_fields.append(f"service_{svc_id}_deleted")
                    continue
                svc_name = svc_data.get("name")
                svc_desc = svc_data.get("description")
                svc_level = svc_data.get("service_level")
                if svc_id and svc_name:
                    svc_obj = BusinessService.query.get(int(svc_id))
                    if svc_obj:
                        has_changed = False
                        if svc_obj.name != svc_name:
                            svc_obj.name = svc_name
                            has_changed = True
                        if svc_obj.description != svc_desc:
                            svc_obj.description = svc_desc
                            has_changed = True
                        if svc_obj.sla_support_level != (svc_level or "silver"):
                            svc_obj.sla_support_level = svc_level or "silver"
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"service_{svc_id}")
                            db.session.add(svc_obj)
        # Process business process updates and deletions
        if form.processes.data:
            from ..models.process_data import BusinessProcess

            for proc_data in form.processes.data:
                proc_id = proc_data.get("id")
                proc_delete = proc_data.get("_delete")
                if proc_id and proc_delete:
                    proc_obj = BusinessProcess.query.get(int(proc_id))
                    if proc_obj:
                        db.session.delete(proc_obj)
                        changed_fields.append(f"process_{proc_id}_deleted")
                    continue
                proc_name = proc_data.get("name")
                proc_code = proc_data.get("process_code")
                proc_owner = proc_data.get("owner")
                if proc_id and proc_name:
                    proc_obj = BusinessProcess.query.get(int(proc_id))
                    if proc_obj:
                        has_changed = False
                        if proc_obj.name != proc_name:
                            proc_obj.name = proc_name
                            has_changed = True
                        if proc_obj.process_code != proc_code:
                            proc_obj.process_code = proc_code
                            has_changed = True
                        if proc_obj.process_owner != proc_owner:
                            proc_obj.process_owner = proc_owner
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"process_{proc_id}")
                            db.session.add(proc_obj)
        # Process business actor updates and deletions
        if form.actors.data:
            from ..models.business_layer import BusinessActor

            for actor_data in form.actors.data:
                actor_id = actor_data.get("id")
                actor_delete = actor_data.get("_delete")
                if actor_id and actor_delete:
                    actor_obj = BusinessActor.query.get(int(actor_id))
                    if actor_obj:
                        db.session.delete(actor_obj)
                        changed_fields.append(f"actor_{actor_id}_deleted")
                    continue
                actor_name = actor_data.get("name")
                actor_type = actor_data.get("actor_type")
                if actor_id and actor_name:
                    actor_obj = BusinessActor.query.get(int(actor_id))
                    if actor_obj:
                        has_changed = False
                        if actor_obj.name != actor_name:
                            actor_obj.name = actor_name
                            has_changed = True
                        if actor_obj.actor_type != (actor_type or "Department"):
                            actor_obj.actor_type = actor_type or "Department"
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"actor_{actor_id}")
                            db.session.add(actor_obj)

        # Process business role updates and deletions
        if form.roles.data:
            from ..models.business_layer import BusinessRole

            for role_data in form.roles.data:
                role_id = role_data.get("id")
                role_delete = role_data.get("_delete")
                if role_id and role_delete:
                    role_obj = BusinessRole.query.get(int(role_id))
                    if role_obj:
                        db.session.delete(role_obj)
                        changed_fields.append(f"role_{role_id}_deleted")
                    continue
                role_name = role_data.get("name")
                role_description = role_data.get("description")
                if role_id and role_name:
                    role_obj = BusinessRole.query.get(int(role_id))
                    if role_obj:
                        has_changed = False
                        if role_obj.name != role_name:
                            role_obj.name = role_name
                            has_changed = True
                        if role_obj.description != (role_description or ""):
                            role_obj.description = role_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"role_{role_id}")
                            db.session.add(role_obj)

        # Process business object updates and deletions
        if form.objects.data:
            from ..models.business_layer import BusinessObject

            for obj_data in form.objects.data:
                obj_id = obj_data.get("id")
                obj_delete = obj_data.get("_delete")
                if obj_id and obj_delete:
                    obj_obj = BusinessObject.query.get(int(obj_id))
                    if obj_obj:
                        db.session.delete(obj_obj)
                        changed_fields.append(f"object_{obj_id}_deleted")
                    continue
                obj_name = obj_data.get("name")
                obj_confidentiality = obj_data.get("confidentiality")
                if obj_id and obj_name:
                    obj_obj = BusinessObject.query.get(int(obj_id))
                    if obj_obj:
                        has_changed = False
                        if obj_obj.name != obj_name:
                            obj_obj.name = obj_name
                            has_changed = True
                        if hasattr(
                            obj_obj, "confidentiality"
                        ) and obj_obj.confidentiality != (obj_confidentiality or ""):
                            obj_obj.confidentiality = obj_confidentiality or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"object_{obj_id}")
                            db.session.add(obj_obj)

        # Process business collaboration updates and deletions
        if form.collaborations.data:
            for collab_data in form.collaborations.data:
                collab_id = collab_data.get("id")
                collab_delete = collab_data.get("_delete")
                if collab_id and collab_delete:
                    collab_obj = BusinessCollaboration.query.get(int(collab_id))
                    if collab_obj:
                        db.session.delete(collab_obj)
                        changed_fields.append(f"collaboration_{collab_id}_deleted")
                    continue
                collab_name = collab_data.get("name")
                collab_description = collab_data.get("description")
                if collab_id and collab_name:
                    collab_obj = BusinessCollaboration.query.get(int(collab_id))
                    if collab_obj:
                        has_changed = False
                        if collab_obj.name != collab_name:
                            collab_obj.name = collab_name
                            has_changed = True
                        if collab_obj.description != (collab_description or ""):
                            collab_obj.description = collab_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"collaboration_{collab_id}")
                            db.session.add(collab_obj)

        # Process business interface updates and deletions
        if form.interfaces.data:
            for intf_data in form.interfaces.data:
                intf_id = intf_data.get("id")
                intf_delete = intf_data.get("_delete")
                if intf_id and intf_delete:
                    intf_obj = BusinessInterface.query.get(int(intf_id))
                    if intf_obj:
                        db.session.delete(intf_obj)
                        changed_fields.append(f"interface_{intf_id}_deleted")
                    continue
                intf_name = intf_data.get("name")
                intf_description = intf_data.get("description")
                if intf_id and intf_name:
                    intf_obj = BusinessInterface.query.get(int(intf_id))
                    if intf_obj:
                        has_changed = False
                        if intf_obj.name != intf_name:
                            intf_obj.name = intf_name
                            has_changed = True
                        if intf_obj.description != (intf_description or ""):
                            intf_obj.description = intf_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"interface_{intf_id}")
                            db.session.add(intf_obj)

        # Process business function updates and deletions
        if form.functions.data:
            from ..models.business_capabilities import BusinessFunction

            for func_data in form.functions.data:
                func_id = func_data.get("id")
                func_delete = func_data.get("_delete")
                if func_id and func_delete:
                    func_obj = BusinessFunction.query.get(int(func_id))
                    if func_obj:
                        db.session.delete(func_obj)
                        changed_fields.append(f"function_{func_id}_deleted")
                    continue
                func_name = func_data.get("name")
                func_description = func_data.get("description")
                if func_id and func_name:
                    func_obj = BusinessFunction.query.get(int(func_id))
                    if func_obj:
                        has_changed = False
                        if func_obj.name != func_name:
                            func_obj.name = func_name
                            has_changed = True
                        if func_obj.description != (func_description or ""):
                            func_obj.description = func_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"function_{func_id}")
                            db.session.add(func_obj)

        # Process business interaction updates and deletions
        if form.interactions.data:
            for inter_data in form.interactions.data:
                inter_id = inter_data.get("id")
                inter_delete = inter_data.get("_delete")
                if inter_id and inter_delete:
                    inter_obj = BusinessInteraction.query.get(int(inter_id))
                    if inter_obj:
                        db.session.delete(inter_obj)
                        changed_fields.append(f"interaction_{inter_id}_deleted")
                    continue
                inter_name = inter_data.get("name")
                inter_description = inter_data.get("description")
                if inter_id and inter_name:
                    inter_obj = BusinessInteraction.query.get(int(inter_id))
                    if inter_obj:
                        has_changed = False
                        if inter_obj.name != inter_name:
                            inter_obj.name = inter_name
                            has_changed = True
                        if inter_obj.description != (inter_description or ""):
                            inter_obj.description = inter_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"interaction_{inter_id}")
                            db.session.add(inter_obj)

        # Process business event updates and deletions
        if form.events.data:
            for event_data in form.events.data:
                event_id = event_data.get("id")
                event_delete = event_data.get("_delete")
                if event_id and event_delete:
                    event_obj = BusinessEvent.query.get(int(event_id))
                    if event_obj:
                        db.session.delete(event_obj)
                        changed_fields.append(f"event_{event_id}_deleted")
                    continue
                event_name = event_data.get("name")
                event_description = event_data.get("description")
                if event_id and event_name:
                    event_obj = BusinessEvent.query.get(int(event_id))
                    if event_obj:
                        has_changed = False
                        if event_obj.name != event_name:
                            event_obj.name = event_name
                            has_changed = True
                        if event_obj.description != (event_description or ""):
                            event_obj.description = event_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"event_{event_id}")
                            db.session.add(event_obj)

        # Process contract updates and deletions
        if form.contracts.data:
            for contract_data in form.contracts.data:
                contract_id = contract_data.get("id")
                contract_delete = contract_data.get("_delete")
                if contract_id and contract_delete:
                    contract_obj = Contract.query.get(int(contract_id))
                    if contract_obj:
                        db.session.delete(contract_obj)
                        changed_fields.append(f"contract_{contract_id}_deleted")
                    continue
                contract_name = contract_data.get("name")
                contract_description = contract_data.get("description")
                if contract_id and contract_name:
                    contract_obj = Contract.query.get(int(contract_id))
                    if contract_obj:
                        has_changed = False
                        if contract_obj.name != contract_name:
                            contract_obj.name = contract_name
                            has_changed = True
                        if contract_obj.description != (contract_description or ""):
                            contract_obj.description = contract_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"contract_{contract_id}")
                            db.session.add(contract_obj)

        # Process representation updates and deletions
        if form.representations.data:
            for repr_data in form.representations.data:
                repr_id = repr_data.get("id")
                repr_delete = repr_data.get("_delete")
                if repr_id and repr_delete:
                    repr_obj = Representation.query.get(int(repr_id))
                    if repr_obj:
                        db.session.delete(repr_obj)
                        changed_fields.append(f"representation_{repr_id}_deleted")
                    continue
                repr_name = repr_data.get("name")
                repr_description = repr_data.get("description")
                if repr_id and repr_name:
                    repr_obj = Representation.query.get(int(repr_id))
                    if repr_obj:
                        has_changed = False
                        if repr_obj.name != repr_name:
                            repr_obj.name = repr_name
                            has_changed = True
                        if repr_obj.description != (repr_description or ""):
                            repr_obj.description = repr_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"representation_{repr_id}")
                            db.session.add(repr_obj)

        db.session.commit()
        if changed_fields:
            try:
                session["business_changes"] = json.dumps(changed_fields)
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug(f"Ignored: {e}")
        return jsonify(
            {
                "success": True,
                "message": f"Business layer updated ({len(changed_fields)} changes)",
                "changed": changed_fields,
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error updating business layer: {str(exc)}")
        return jsonify({"success": False, "error": str(exc)}), 500


@application_mgmt.route(
    "/applications/<int:id>/layers/application-update", methods=["POST"]
)
@login_required
def update_application_layer(id):
    """Update Application Layer elements (Interfaces, Data Objects, Services)"""
    ApplicationComponent.query.get_or_404(id)
    # csrf-ok: global CSRFProtect active
    try:
        form = ApplicationLayerForm(request.form)
        if not form.validate():

            def format_errors(errors_dict):
                formatted = []
                for field, msgs in errors_dict.items():
                    if isinstance(msgs, dict):
                        for sub_field, sub_msgs in msgs.items():
                            if isinstance(sub_msgs, list):
                                formatted.append(
                                    f"{field}.{sub_field}: {', '.join(str(m) for m in sub_msgs)}"
                                )
                            else:
                                formatted.append(f"{field}.{sub_field}: {sub_msgs}")
                    elif isinstance(msgs, list):
                        formatted.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        formatted.append(f"{field}: {msgs}")
                return formatted

            errors = format_errors(form.errors)
            return jsonify({"success": False, "errors": errors}), 400
        changed_fields = []
        # Process interface updates and deletions
        if form.interfaces.data:
            from ..models.application_layer import ApplicationInterface

            for iface_data in form.interfaces.data:
                iface_id = iface_data.get("id")
                iface_delete = iface_data.get("_delete")
                if iface_id and iface_delete:
                    iface_obj = ApplicationInterface.query.get(int(iface_id))
                    if iface_obj:
                        db.session.delete(iface_obj)
                        changed_fields.append(f"interface_{iface_id}_deleted")
                    continue
                iface_name = iface_data.get("name")
                iface_type = iface_data.get("interface_type")
                iface_protocol = iface_data.get("protocol")
                if iface_id and iface_name:
                    iface_obj = ApplicationInterface.query.get(int(iface_id))
                    if iface_obj:
                        has_changed = False
                        if iface_obj.name != iface_name:
                            iface_obj.name = iface_name
                            has_changed = True
                        if iface_obj.interface_type != (iface_type or "api"):
                            iface_obj.interface_type = iface_type or "api"
                            has_changed = True
                        if iface_obj.protocol != (iface_protocol or ""):
                            iface_obj.protocol = iface_protocol or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"interface_{iface_id}")
                            db.session.add(iface_obj)
        # Process data object updates and deletions
        if form.data_objects.data:
            from ..models.application_layer import DataObject

            for dobj_data in form.data_objects.data:
                dobj_id = dobj_data.get("id")
                dobj_delete = dobj_data.get("_delete")
                if dobj_id and dobj_delete:
                    dobj_obj = DataObject.query.get(int(dobj_id))
                    if dobj_obj:
                        db.session.delete(dobj_obj)
                        changed_fields.append(f"dataobject_{dobj_id}_deleted")
                    continue
                dobj_name = dobj_data.get("name")
                dobj_format = dobj_data.get("data_format")
                dobj_persistence = dobj_data.get("persistence")
                if dobj_id and dobj_name:
                    dobj_obj = DataObject.query.get(int(dobj_id))
                    if dobj_obj:
                        has_changed = False
                        if dobj_obj.name != dobj_name:
                            dobj_obj.name = dobj_name
                            has_changed = True
                        if dobj_obj.data_format != (dobj_format or "Relational"):
                            dobj_obj.data_format = dobj_format or "Relational"
                            has_changed = True
                        if (
                            hasattr(dobj_obj, "persistence_layer")
                            and dobj_obj.persistence_layer != dobj_persistence
                        ):
                            dobj_obj.persistence_layer = dobj_persistence
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"dataobject_{dobj_id}")
                            db.session.add(dobj_obj)

        # Process application service updates and deletions
        if form.services.data:
            from ..models.application_layer import ApplicationService

            for svc_data in form.services.data:
                svc_id = svc_data.get("id")
                svc_delete = svc_data.get("_delete")
                if svc_id and svc_delete:
                    svc_obj = ApplicationService.query.get(int(svc_id))
                    if svc_obj:
                        db.session.delete(svc_obj)
                        changed_fields.append(f"service_{svc_id}_deleted")
                    continue
                svc_name = svc_data.get("name")
                svc_type = svc_data.get("service_type")
                if svc_id and svc_name:
                    svc_obj = ApplicationService.query.get(int(svc_id))
                    if svc_obj:
                        has_changed = False
                        if svc_obj.name != svc_name:
                            svc_obj.name = svc_name
                            has_changed = True
                        if svc_obj.service_type != (svc_type or "Shared"):
                            svc_obj.service_type = svc_type or "Shared"
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"service_{svc_id}")
                            db.session.add(svc_obj)

        # Process application function updates and deletions
        if form.functions.data:
            from ..models.application_layer import ApplicationFunction

            for func_data in form.functions.data:
                func_id = func_data.get("id")
                func_delete = func_data.get("_delete")
                if func_id and func_delete:
                    func_obj = ApplicationFunction.query.get(int(func_id))
                    if func_obj:
                        db.session.delete(func_obj)
                        changed_fields.append(f"function_{func_id}_deleted")
                    continue
                func_name = func_data.get("name")
                func_desc = func_data.get("description")
                if func_id and func_name:
                    func_obj = ApplicationFunction.query.get(int(func_id))
                    if func_obj:
                        has_changed = False
                        if func_obj.name != func_name:
                            func_obj.name = func_name
                            has_changed = True
                        if func_obj.description != (func_desc or ""):
                            func_obj.description = func_desc or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"function_{func_id}")
                            db.session.add(func_obj)

        # Process application process updates and deletions
        if form.processes.data:
            from ..models.application_layer import ApplicationProcess

            for proc_data in form.processes.data:
                proc_id = proc_data.get("id")
                proc_delete = proc_data.get("_delete")
                if proc_id and proc_delete:
                    proc_obj = ApplicationProcess.query.get(int(proc_id))
                    if proc_obj:
                        db.session.delete(proc_obj)
                        changed_fields.append(f"process_{proc_id}_deleted")
                    continue
                proc_name = proc_data.get("name")
                proc_desc = proc_data.get("description")
                if proc_id and proc_name:
                    proc_obj = ApplicationProcess.query.get(int(proc_id))
                    if proc_obj:
                        has_changed = False
                        if proc_obj.name != proc_name:
                            proc_obj.name = proc_name
                            has_changed = True
                        if proc_obj.description != (proc_desc or ""):
                            proc_obj.description = proc_desc or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"process_{proc_id}")
                            db.session.add(proc_obj)

        # Process application collaboration updates and deletions
        if form.collaborations.data:
            from ..models.application_layer import ApplicationCollaboration

            for collab_data in form.collaborations.data:
                collab_id = collab_data.get("id")
                collab_delete = collab_data.get("_delete")
                if collab_id and collab_delete:
                    collab_obj = ApplicationCollaboration.query.get(int(collab_id))
                    if collab_obj:
                        db.session.delete(collab_obj)
                        changed_fields.append(f"collaboration_{collab_id}_deleted")
                    continue
                collab_name = collab_data.get("name")
                collab_desc = collab_data.get("description")
                if collab_id and collab_name:
                    collab_obj = ApplicationCollaboration.query.get(int(collab_id))
                    if collab_obj:
                        has_changed = False
                        if collab_obj.name != collab_name:
                            collab_obj.name = collab_name
                            has_changed = True
                        if collab_obj.description != (collab_desc or ""):
                            collab_obj.description = collab_desc or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"collaboration_{collab_id}")
                            db.session.add(collab_obj)

        # Process application interaction updates and deletions
        if form.interactions.data:
            from ..models.application_layer import ApplicationInteraction

            for inter_data in form.interactions.data:
                inter_id = inter_data.get("id")
                inter_delete = inter_data.get("_delete")
                if inter_id and inter_delete:
                    inter_obj = ApplicationInteraction.query.get(int(inter_id))
                    if inter_obj:
                        db.session.delete(inter_obj)
                        changed_fields.append(f"interaction_{inter_id}_deleted")
                    continue
                inter_name = inter_data.get("name")
                inter_desc = inter_data.get("description")
                if inter_id and inter_name:
                    inter_obj = ApplicationInteraction.query.get(int(inter_id))
                    if inter_obj:
                        has_changed = False
                        if inter_obj.name != inter_name:
                            inter_obj.name = inter_name
                            has_changed = True
                        if inter_obj.description != (inter_desc or ""):
                            inter_obj.description = inter_desc or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"interaction_{inter_id}")
                            db.session.add(inter_obj)

        # Process application event updates and deletions
        if form.events.data:
            from ..models.application_layer import ApplicationEvent

            for evt_data in form.events.data:
                evt_id = evt_data.get("id")
                evt_delete = evt_data.get("_delete")
                if evt_id and evt_delete:
                    evt_obj = ApplicationEvent.query.get(int(evt_id))
                    if evt_obj:
                        db.session.delete(evt_obj)
                        changed_fields.append(f"event_{evt_id}_deleted")
                    continue
                evt_name = evt_data.get("name")
                evt_desc = evt_data.get("description")
                if evt_id and evt_name:
                    evt_obj = ApplicationEvent.query.get(int(evt_id))
                    if evt_obj:
                        has_changed = False
                        if evt_obj.name != evt_name:
                            evt_obj.name = evt_name
                            has_changed = True
                        if evt_obj.description != (evt_desc or ""):
                            evt_obj.description = evt_desc or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"event_{evt_id}")
                            db.session.add(evt_obj)

        db.session.commit()
        if changed_fields:
            try:
                session["application_changes"] = json.dumps(changed_fields)
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug(f"Ignored: {e}")
        return jsonify(
            {
                "success": True,
                "message": f"Application layer updated ({len(changed_fields)} changes)",
                "changed": changed_fields,
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error updating application layer: {str(exc)}")
        return jsonify({"success": False, "error": str(exc)}), 500


@application_mgmt.route(
    "/applications/<int:id>/layers/technology-update", methods=["POST"]
)
@login_required
def update_technology_layer(id):
    """Update Technology Layer elements (Nodes, SystemSoftware, TechnologyServices)"""
    ApplicationComponent.query.get_or_404(id)
    # csrf-ok: global CSRFProtect active
    try:
        form = TechnologyLayerForm(request.form)
        if not form.validate():

            def format_errors(errors_dict):
                formatted = []
                for field, msgs in errors_dict.items():
                    if isinstance(msgs, dict):
                        for sub_field, sub_msgs in msgs.items():
                            if isinstance(sub_msgs, list):
                                formatted.append(
                                    f"{field}.{sub_field}: {', '.join(str(m) for m in sub_msgs)}"
                                )
                            else:
                                formatted.append(f"{field}.{sub_field}: {sub_msgs}")
                    elif isinstance(msgs, list):
                        formatted.append(f"{field}: {', '.join(str(m) for m in msgs)}")
                    else:
                        formatted.append(f"{field}: {msgs}")
                return formatted

            errors = format_errors(form.errors)
            return jsonify({"success": False, "errors": errors}), 400
        changed_fields = []
        # Process node updates and deletions
        if form.nodes.data:
            # Node already imported at top
            for node_data in form.nodes.data:
                node_id = node_data.get("id")
                node_delete = node_data.get("_delete")
                if node_id and node_delete:
                    node_obj = Node.query.get(int(node_id))
                    if node_obj:
                        db.session.delete(node_obj)
                        changed_fields.append(f"node_{node_id}_deleted")
                    continue
                node_name = node_data.get("name")
                node_type = node_data.get("node_type")
                node_loc = node_data.get("location")
                if node_id and node_name:
                    node_obj = Node.query.get(int(node_id))
                    if node_obj:
                        has_changed = False
                        if node_obj.name != node_name:
                            node_obj.name = node_name
                            has_changed = True
                        if node_obj.node_type != (node_type or "virtual"):
                            node_obj.node_type = node_type or "virtual"
                            has_changed = True
                        if (
                            hasattr(node_obj, "datacenter")
                            and node_obj.datacenter != node_loc
                        ):
                            node_obj.datacenter = node_loc
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"node_{node_id}")
                            db.session.add(node_obj)
        # Process system software updates and deletions
        if form.system_software.data:
            # SystemSoftware already imported at top
            for sw_data in form.system_software.data:
                sw_id = sw_data.get("id")
                sw_delete = sw_data.get("_delete")
                if sw_id and sw_delete:
                    sw_obj = SystemSoftware.query.get(int(sw_id))
                    if sw_obj:
                        db.session.delete(sw_obj)
                        changed_fields.append(f"systemsoftware_{sw_id}_deleted")
                    continue
                sw_name = sw_data.get("name")
                sw_type = sw_data.get("software_type")
                sw_version = sw_data.get("version")
                if sw_id and sw_name:
                    sw_obj = SystemSoftware.query.get(int(sw_id))
                    if sw_obj:
                        has_changed = False
                        if sw_obj.name != sw_name:
                            sw_obj.name = sw_name
                            has_changed = True
                        if sw_obj.software_type != (sw_type or "os"):
                            sw_obj.software_type = sw_type or "os"
                            has_changed = True
                        if sw_obj.version != sw_version:
                            sw_obj.version = sw_version
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"systemsoftware_{sw_id}")
                            db.session.add(sw_obj)
        # Process technology service updates and deletions
        if form.technology_services.data:
            # TechnologyService already imported at top
            for tsvc_data in form.technology_services.data:
                tsvc_id = tsvc_data.get("id")
                tsvc_delete = tsvc_data.get("_delete")
                if tsvc_id and tsvc_delete:
                    tsvc_obj = TechnologyService.query.get(int(tsvc_id))
                    if tsvc_obj:
                        db.session.delete(tsvc_obj)
                        changed_fields.append(f"technologyservice_{tsvc_id}_deleted")
                    continue
                tsvc_name = tsvc_data.get("name")
                tsvc_type = tsvc_data.get("service_type")
                if tsvc_id and tsvc_name:
                    tsvc_obj = TechnologyService.query.get(int(tsvc_id))
                    if tsvc_obj:
                        old_name = tsvc_obj.name
                        tsvc_obj.name = tsvc_name
                        tsvc_obj.service_type = tsvc_type or "compute"
                        if old_name != tsvc_name:
                            changed_fields.append(f"technologyservice_{tsvc_id}")
                        db.session.add(tsvc_obj)

        # Process device updates and deletions
        if form.devices.data:
            # Device already imported at top
            for dev_data in form.devices.data:
                dev_id = dev_data.get("id")
                dev_delete = dev_data.get("_delete")
                if dev_id and dev_delete:
                    dev_obj = Device.query.get(int(dev_id))
                    if dev_obj:
                        db.session.delete(dev_obj)
                        changed_fields.append(f"device_{dev_id}_deleted")
                    continue
                dev_name = dev_data.get("name")
                dev_type = dev_data.get("device_type")
                if dev_id and dev_name:
                    dev_obj = Device.query.get(int(dev_id))
                    if dev_obj:
                        has_changed = False
                        if dev_obj.name != dev_name:
                            dev_obj.name = dev_name
                            has_changed = True
                        if hasattr(dev_obj, "device_type") and dev_obj.device_type != (
                            dev_type or ""
                        ):
                            dev_obj.device_type = dev_type or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"device_{dev_id}")
                            db.session.add(dev_obj)

        # Process technology interface updates and deletions
        if form.technology_interfaces.data:
            # TechnologyInterface already imported at top
            for intf_data in form.technology_interfaces.data:
                intf_id = intf_data.get("id")
                intf_delete = intf_data.get("_delete")
                if intf_id and intf_delete:
                    intf_obj = TechnologyInterface.query.get(int(intf_id))
                    if intf_obj:
                        db.session.delete(intf_obj)
                        changed_fields.append(f"interface_{intf_id}_deleted")
                    continue
                intf_name = intf_data.get("name")
                intf_description = intf_data.get("description")
                if intf_id and intf_name:
                    intf_obj = TechnologyInterface.query.get(int(intf_id))
                    if intf_obj:
                        has_changed = False
                        if intf_obj.name != intf_name:
                            intf_obj.name = intf_name
                            has_changed = True
                        if intf_obj.description != (intf_description or ""):
                            intf_obj.description = intf_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"interface_{intf_id}")
                            db.session.add(intf_obj)

        # Process path updates and deletions
        if form.paths.data:
            # Path already imported at top
            for path_data in form.paths.data:
                path_id = path_data.get("id")
                path_delete = path_data.get("_delete")
                if path_id and path_delete:
                    path_obj = Path.query.get(int(path_id))
                    if path_obj:
                        db.session.delete(path_obj)
                        changed_fields.append(f"path_{path_id}_deleted")
                    continue
                path_name = path_data.get("name")
                path_description = path_data.get("description")
                if path_id and path_name:
                    path_obj = Path.query.get(int(path_id))
                    if path_obj:
                        has_changed = False
                        if path_obj.name != path_name:
                            path_obj.name = path_name
                            has_changed = True
                        if path_obj.description != (path_description or ""):
                            path_obj.description = path_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"path_{path_id}")
                            db.session.add(path_obj)

        # Process communication network updates and deletions
        if form.communication_networks.data:
            # CommunicationNetwork already imported at top
            for network_data in form.communication_networks.data:
                network_id = network_data.get("id")
                network_delete = network_data.get("_delete")
                if network_id and network_delete:
                    network_obj = CommunicationNetwork.query.get(int(network_id))
                    if network_obj:
                        db.session.delete(network_obj)
                        changed_fields.append(f"network_{network_id}_deleted")
                    continue
                network_name = network_data.get("name")
                network_description = network_data.get("description")
                if network_id and network_name:
                    network_obj = CommunicationNetwork.query.get(int(network_id))
                    if network_obj:
                        has_changed = False
                        if network_obj.name != network_name:
                            network_obj.name = network_name
                            has_changed = True
                        if network_obj.description != (network_description or ""):
                            network_obj.description = network_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"network_{network_id}")
                            db.session.add(network_obj)

        # Process technology collaboration updates and deletions
        if form.technology_collaborations.data:
            for collab_data in form.technology_collaborations.data:
                collab_id = collab_data.get("id")
                collab_delete = collab_data.get("_delete")
                if collab_id and collab_delete:
                    collab_obj = TechnologyCollaborationFull.query.get(int(collab_id))
                    if collab_obj:
                        db.session.delete(collab_obj)
                        changed_fields.append(f"collaboration_{collab_id}_deleted")
                    continue
                collab_name = collab_data.get("name")
                collab_description = collab_data.get("description")
                if collab_id and collab_name:
                    collab_obj = TechnologyCollaborationFull.query.get(int(collab_id))
                    if collab_obj:
                        has_changed = False
                        if collab_obj.name != collab_name:
                            collab_obj.name = collab_name
                            has_changed = True
                        if collab_obj.description != (collab_description or ""):
                            collab_obj.description = collab_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"collaboration_{collab_id}")
                            db.session.add(collab_obj)

        # Process technology function updates and deletions
        if form.technology_functions.data:
            for func_data in form.technology_functions.data:
                func_id = func_data.get("id")
                func_delete = func_data.get("_delete")
                if func_id and func_delete:
                    func_obj = TechnologyFunction.query.get(int(func_id))
                    if func_obj:
                        db.session.delete(func_obj)
                        changed_fields.append(f"function_{func_id}_deleted")
                    continue
                func_name = func_data.get("name")
                func_description = func_data.get("description")
                if func_id and func_name:
                    func_obj = TechnologyFunction.query.get(int(func_id))
                    if func_obj:
                        has_changed = False
                        if func_obj.name != func_name:
                            func_obj.name = func_name
                            has_changed = True
                        if func_obj.description != (func_description or ""):
                            func_obj.description = func_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"function_{func_id}")
                            db.session.add(func_obj)

        # Process technology process updates and deletions
        if form.technology_processes.data:
            for proc_data in form.technology_processes.data:
                proc_id = proc_data.get("id")
                proc_delete = proc_data.get("_delete")
                if proc_id and proc_delete:
                    proc_obj = TechnologyProcess.query.get(int(proc_id))
                    if proc_obj:
                        db.session.delete(proc_obj)
                        changed_fields.append(f"process_{proc_id}_deleted")
                    continue
                proc_name = proc_data.get("name")
                proc_description = proc_data.get("description")
                if proc_id and proc_name:
                    proc_obj = TechnologyProcess.query.get(int(proc_id))
                    if proc_obj:
                        has_changed = False
                        if proc_obj.name != proc_name:
                            proc_obj.name = proc_name
                            has_changed = True
                        if proc_obj.description != (proc_description or ""):
                            proc_obj.description = proc_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"process_{proc_id}")
                            db.session.add(proc_obj)

        # Process technology interaction updates and deletions
        if form.technology_interactions.data:
            for inter_data in form.technology_interactions.data:
                inter_id = inter_data.get("id")
                inter_delete = inter_data.get("_delete")
                if inter_id and inter_delete:
                    inter_obj = TechnologyInteraction.query.get(int(inter_id))
                    if inter_obj:
                        db.session.delete(inter_obj)
                        changed_fields.append(f"interaction_{inter_id}_deleted")
                    continue
                inter_name = inter_data.get("name")
                inter_description = inter_data.get("description")
                if inter_id and inter_name:
                    inter_obj = TechnologyInteraction.query.get(int(inter_id))
                    if inter_obj:
                        has_changed = False
                        if inter_obj.name != inter_name:
                            inter_obj.name = inter_name
                            has_changed = True
                        if inter_obj.description != (inter_description or ""):
                            inter_obj.description = inter_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"interaction_{inter_id}")
                            db.session.add(inter_obj)

        # Process technology event updates and deletions
        if form.technology_events.data:
            for event_data in form.technology_events.data:
                event_id = event_data.get("id")
                event_delete = event_data.get("_delete")
                if event_id and event_delete:
                    event_obj = TechnologyEvent.query.get(int(event_id))
                    if event_obj:
                        db.session.delete(event_obj)
                        changed_fields.append(f"event_{event_id}_deleted")
                    continue
                event_name = event_data.get("name")
                event_description = event_data.get("description")
                if event_id and event_name:
                    event_obj = TechnologyEvent.query.get(int(event_id))
                    if event_obj:
                        has_changed = False
                        if event_obj.name != event_name:
                            event_obj.name = event_name
                            has_changed = True
                        if event_obj.description != (event_description or ""):
                            event_obj.description = event_description or ""
                            has_changed = True
                        if has_changed:
                            changed_fields.append(f"event_{event_id}")
                            db.session.add(event_obj)

        db.session.commit()
        if changed_fields:
            try:
                session["technology_changes"] = json.dumps(changed_fields)
            except Exception as e:  # fabricated-ok: guarded skip on error; emits no fabricated value
                logger.debug(f"Ignored: {e}")
        return jsonify(
            {
                "success": True,
                "message": f"Technology layer updated ({len(changed_fields)} changes)",
                "changed": changed_fields,
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error updating technology layer: {str(exc)}")
        return jsonify({"success": False, "error": str(exc)}), 500
