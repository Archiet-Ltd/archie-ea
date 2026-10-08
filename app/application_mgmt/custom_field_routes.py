"""
Custom Fields Management Routes

Admin interface to create, edit, and manage custom fields
"""

import json
import logging
import re

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from app.decorators import audit_log, require_roles
from app.middleware.tenant_decorators import platform_admin_required
from app.utils.tenant_users import escape_like_literal

from .. import db
from ..models.custom_fields import ApplicationCustomFieldValue, CustomFieldDefinition
from . import application_mgmt

logger = logging.getLogger(__name__)

# --- Custom Field Validation ---
_FIELD_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
_VALID_FIELD_TYPES = {
    "text", "textarea", "number", "date", "boolean",
    "select", "multiselect", "url", "email", "integer", "float",
}
_MAX_REGEX_LENGTH = 200


def _validate_custom_field(form_data):
    """Validate custom field form data. Returns (errors, sanitized_data)."""
    errors = []

    field_name = form_data.get("field_name", "").strip()
    if not field_name:
        errors.append("Field name is required.")
    elif not _FIELD_NAME_PATTERN.match(field_name):
        errors.append(
            "Field name must start with a letter and contain only letters, digits, and underscores (max 64 chars)."
        )

    field_label = form_data.get("field_label", "").strip()
    if not field_label:
        errors.append("Field label is required.")
    elif len(field_label) > 255:
        errors.append("Field label must be 255 characters or fewer.")

    field_type = form_data.get("field_type", "").strip()
    if field_type and field_type not in _VALID_FIELD_TYPES:
        errors.append(f"Invalid field type: {field_type}")

    regex_pattern = form_data.get("regex_pattern", "").strip()
    if regex_pattern:
        if len(regex_pattern) > _MAX_REGEX_LENGTH:
            errors.append(f"Regex pattern must be {_MAX_REGEX_LENGTH} characters or fewer.")
        else:
            try:
                re.compile(regex_pattern)
            except re.error as e:
                errors.append(f"Invalid regex pattern: {e}")

    help_text = form_data.get("help_text", "").strip()
    if help_text and len(help_text) > 1000:
        errors.append("Help text must be 1000 characters or fewer.")

    return errors


@application_mgmt.route("/admin/custom-fields")
@login_required
@require_roles("admin")
def custom_fields_list():
    """List all custom field definitions with filtering and search"""

    # Get query parameters
    status_filter = request.args.get("status", "all")
    type_filter = request.args.get("type", "all")
    group_filter = request.args.get("group", "all")
    search_query = request.args.get("search", "")

    # Build query
    query = CustomFieldDefinition.query.filter_by(entity_type="application_component")

    # Apply filters
    if status_filter == "active":
        query = query.filter_by(is_active=True)
    elif status_filter == "inactive":
        query = query.filter_by(is_active=False)

    if type_filter != "all":
        query = query.filter_by(field_type=type_filter)

    if group_filter != "all":
        query = query.filter_by(field_group=group_filter)

    if search_query:
        _escaped = escape_like_literal(search_query)
        search_pattern = f"%{_escaped}%"
        query = query.filter(
            db.or_(
                CustomFieldDefinition.field_label.ilike(search_pattern, escape="\\"),
                CustomFieldDefinition.field_name.ilike(search_pattern, escape="\\"),
                CustomFieldDefinition.help_text.ilike(search_pattern, escape="\\"),
            )
        )

    # Order by display order and label
    fields = query.order_by(
        CustomFieldDefinition.display_order, CustomFieldDefinition.field_label
    ).all()

    # Calculate statistics
    total_fields = CustomFieldDefinition.query.filter_by(
        entity_type="application_component"
    ).count()

    active_fields = CustomFieldDefinition.query.filter_by(
        entity_type="application_component", is_active=True
    ).count()

    # Get unique field types and groups for filters
    all_fields = CustomFieldDefinition.query.filter_by(
        entity_type="application_component"
    ).all()

    field_types = sorted(list(set(f.field_type for f in all_fields if f.field_type)))
    field_groups = sorted(list(set(f.field_group for f in all_fields if f.field_group)))

    return render_template(
        "applications/dashboard.html",
        fields=fields,
        total_fields=total_fields,
        active_fields=active_fields,
        field_types=field_types,
        field_groups=field_groups,
        status_filter=status_filter,
        type_filter=type_filter,
        group_filter=group_filter,
        search_query=search_query,
    )


@application_mgmt.route("/admin/custom-fields/create", methods=["GET", "POST"])
@login_required
@require_roles("admin")
@platform_admin_required
def custom_field_create():
    """Create a new custom field definition

    Platform-admin-only (lead review of PR 430, Part 3): writes the global
    ``custom_field_definitions`` table, which carries no ``organization_id``
    at all. ``require_roles("admin")`` is satisfied by any org's own admin
    for their own brand-new, self-registered organisation -- no invitation
    into anyone else's org needed. ``require_roles`` is kept rather than
    removed; ``platform_admin_required`` is the operative check.
    """
    if request.method == "GET":
        # Template requires WTForms, redirect to list page
        return redirect(url_for("application_mgmt.custom_fields_list"))

    if request.method == "POST":
        try:
            # Validate input
            validation_errors = _validate_custom_field(request.form)
            if validation_errors:
                flash(" ".join(validation_errors), "error")
                return redirect(url_for("application_mgmt.custom_fields_list"))

            # Parse options if provided
            options = None
            if request.form.get("field_type") in ["select", "multiselect"]:
                options_text = request.form.get("options", "")
                if options_text:
                    # Split by newlines or commas
                    options_list = [
                        opt.strip()
                        for opt in options_text.replace("\n", ",").split(",")
                        if opt.strip()
                    ]
                    if len(options_list) > 200:
                        flash("Too many options (max 200).", "error")
                        return redirect(url_for("application_mgmt.custom_fields_list"))
                    options = json.dumps(options_list)

            field = CustomFieldDefinition(
                field_name=request.form.get("field_name").strip(),
                field_label=request.form.get("field_label").strip(),
                field_type=request.form.get("field_type"),
                help_text=request.form.get("help_text"),
                is_required=request.form.get("is_required") == "on",
                is_searchable=request.form.get("is_searchable") == "on",
                is_shown_in_list=request.form.get("is_shown_in_list") == "on",
                default_value=request.form.get("default_value"),
                options=options,
                min_value=float(request.form.get("min_value"))
                if request.form.get("min_value")
                else None,
                max_value=float(request.form.get("max_value"))
                if request.form.get("max_value")
                else None,
                min_length=int(request.form.get("min_length"))
                if request.form.get("min_length")
                else None,
                max_length=int(request.form.get("max_length"))
                if request.form.get("max_length")
                else None,
                regex_pattern=request.form.get("regex_pattern"),
                display_order=int(request.form.get("display_order", 0)),
                field_group=request.form.get("field_group"),
                entity_type="application_component",
            )

            db.session.add(field)
            db.session.commit()

            flash(
                f'Custom field "{field.field_label}" created successfully!', "success"
            )
            return redirect(url_for("application_mgmt.custom_fields_list"))

        except Exception:
            db.session.rollback()
            flash("Error creating custom field. Please try again.", "error")
            return redirect(url_for("application_mgmt.custom_fields_list"))


@application_mgmt.route("/admin/custom-fields/<int:id>/edit", methods=["GET", "POST"])
@login_required
@require_roles("admin")
@platform_admin_required
def custom_field_edit(id):
    """Edit custom field definition

    Platform-admin-only -- see ``custom_field_create``'s docstring above.
    """
    field = CustomFieldDefinition.query.get_or_404(id)

    if request.method == "POST":
        try:
            # Validate input
            validation_errors = _validate_custom_field(request.form)
            if validation_errors:
                flash(" ".join(validation_errors), "error")
                return redirect(url_for("application_mgmt.custom_field_edit", id=id))

            # Parse options if provided
            options = None
            if request.form.get("field_type") in ["select", "multiselect"]:
                options_text = request.form.get("options", "")
                if options_text:
                    options_list = [
                        opt.strip()
                        for opt in options_text.replace("\n", ",").split(",")
                        if opt.strip()
                    ]
                    if len(options_list) > 200:
                        flash("Too many options (max 200).", "error")
                        return redirect(url_for("application_mgmt.custom_field_edit", id=id))
                    options = json.dumps(options_list)

            field.field_name = request.form.get("field_name").strip()
            field.field_label = request.form.get("field_label").strip()
            field.field_type = request.form.get("field_type")
            field.help_text = request.form.get("help_text", "").strip()[:1000]
            field.is_required = request.form.get("is_required") == "on"
            field.is_searchable = request.form.get("is_searchable") == "on"
            field.is_shown_in_list = request.form.get("is_shown_in_list") == "on"
            field.default_value = request.form.get("default_value")
            field.options = options
            field.min_value = (
                float(request.form.get("min_value"))
                if request.form.get("min_value")
                else None
            )
            field.max_value = (
                float(request.form.get("max_value"))
                if request.form.get("max_value")
                else None
            )
            field.min_length = (
                int(request.form.get("min_length"))
                if request.form.get("min_length")
                else None
            )
            field.max_length = (
                int(request.form.get("max_length"))
                if request.form.get("max_length")
                else None
            )
            field.regex_pattern = request.form.get("regex_pattern")
            field.display_order = int(
                request.form.get("display_order", field.display_order)
            )
            field.field_group = request.form.get("field_group")

            db.session.commit()

            flash(
                f'Custom field "{field.field_label}" updated successfully!', "success"
            )
            return redirect(url_for("application_mgmt.custom_fields_list"))

        except Exception:
            db.session.rollback()
            flash("Error updating custom field. Please try again.", "error")

    # application_mgmt/custom_field_form.html is a WTForms template - it opens with
    # form.hidden_tag() and makes 17 form.* references - but this route builds no
    # form object (it reads request.form directly), and no CustomField*Form class
    # exists anywhere in the tree. Rendering it therefore raised UndefinedError and
    # returned a hard 500 on every GET of this page.
    #
    # custom_field_create() above hit the identical problem and resolved it at
    # :150-152 with "Template requires WTForms, redirect to list page". Doing the
    # same here makes the two halves of this admin screen behave consistently and
    # turns a 500 into the already-accepted degraded behaviour.
    #
    # The real fix is to build the form (or rewrite the template against the raw
    # field names this route already parses); until then editing is unavailable
    # rather than broken.
    flash(
        "Editing a custom field is not available yet. Delete and recreate the "
        "field to change it.",
        "warning",
    )
    return redirect(url_for("application_mgmt.custom_fields_list"))


@application_mgmt.route("/admin/custom-fields/<int:id>/delete", methods=["POST"])
@login_required
@require_roles("admin")
@platform_admin_required
@audit_log("custom_field_delete")
def custom_field_delete(id):
    """Delete or deactivate custom field

    Platform-admin-only -- see ``custom_field_create``'s docstring above.
    """
    field = CustomFieldDefinition.query.get_or_404(id)

    # Check if field has values
    values_count = field.values.count()

    if values_count > 0:
        # Deactivate instead of delete if has values
        field.is_active = False
        db.session.commit()
        flash(
            f'Custom field "{field.field_label}" deactivated (had {values_count} values)',
            "warning",
        )
    else:
        # Safe to delete
        field_label = field.field_label
        db.session.delete(field)
        db.session.commit()
        flash(f'Custom field "{field_label}" deleted successfully!', "success")

    return redirect(url_for("application_mgmt.custom_fields_list"))


@application_mgmt.route("/admin/custom-fields/bulk-delete", methods=["POST"])
@login_required
@require_roles("admin")
@platform_admin_required
@audit_log("custom_fields_bulk_delete")
def custom_fields_bulk_delete():
    """Bulk delete or deactivate custom fields

    Platform-admin-only -- see ``custom_field_create``'s docstring above.
    """
    field_ids = request.form.getlist("field_ids")

    if not field_ids:
        flash("No fields selected", "error")
        return redirect(url_for("application_mgmt.custom_fields_list"))

    deleted_count = 0
    deactivated_count = 0

    # Batch-prefetch value counts to avoid N+1 queries
    try:
        int_field_ids = [int(fid) for fid in field_ids]
    except (ValueError, TypeError):
        flash("field_ids must be integers", "error")
        return redirect(url_for("application_mgmt.custom_fields_list"))
    value_counts = dict(
        db.session.query(
            ApplicationCustomFieldValue.field_definition_id,
            func.count(ApplicationCustomFieldValue.id),
        )
        .filter(ApplicationCustomFieldValue.field_definition_id.in_(int_field_ids))
        .group_by(ApplicationCustomFieldValue.field_definition_id)
        .all()
    )
    fields = CustomFieldDefinition.query.filter(
        CustomFieldDefinition.id.in_(int_field_ids)
    ).all()

    for field in fields:
        try:
            values_count = value_counts.get(field.id, 0)

            if values_count > 0:
                # Deactivate instead of delete if has values
                field.is_active = False
                deactivated_count += 1
            else:
                # Safe to delete
                db.session.delete(field)
                deleted_count += 1
        except Exception:
            continue

    db.session.commit()

    messages = []
    if deleted_count > 0:
        messages.append(f"{deleted_count} field(s) deleted")
    if deactivated_count > 0:
        messages.append(f"{deactivated_count} field(s) deactivated (had values)")

    flash(
        ", ".join(messages) if messages else "No fields were deleted",
        "success" if messages else "warning",
    )
    return redirect(url_for("application_mgmt.custom_fields_list"))
