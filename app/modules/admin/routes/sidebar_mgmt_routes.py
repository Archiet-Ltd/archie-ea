"""
Admin Sidebar Management Routes (migrated).

Migrated from: app/admin/sidebar_mgmt_routes.py
Blueprint name preserved: "sidebar_mgmt"
URL prefix preserved: /api/admin/sidebar
"""
import logging

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.decorators import admin_required, audit_log
from app.middleware.tenant_decorators import platform_admin_required
from app.extensions import db
from app.models.sidebar_menu import SidebarMenuItem
from app.services.sidebar_menu_audit_log import SidebarMenuAuditLog
from app.services.sidebar_menu_validator import SidebarMenuValidator

logger = logging.getLogger(__name__)

sidebar_mgmt_bp = Blueprint("sidebar_mgmt", __name__, url_prefix="/api/admin/sidebar")


@sidebar_mgmt_bp.route("/items", methods=["GET"])
@login_required
@admin_required
def list_sidebar_items():
    """List all sidebar menu items with hierarchical grouping."""
    items = SidebarMenuItem.query.order_by(
        SidebarMenuItem.section, SidebarMenuItem.level, SidebarMenuItem.order
    ).all()

    grouped = {}
    for item in items:
        section = item.section
        if section not in grouped:
            grouped[section] = {}

        subsection = item.subsection or "Main"
        if subsection not in grouped[section]:
            grouped[section][subsection] = []

        grouped[section][subsection].append(item.to_dict())

    is_healthy, errors = SidebarMenuValidator.validate_all_items()

    return jsonify(
        {
            "status": "success",
            "items": grouped,
            "total": len(items),
            "enabled": sum(1 for i in items if i.is_enabled),
            "disabled": sum(1 for i in items if not i.is_enabled),
            "items_with_role_restrictions": sum(1 for i in items if i.required_roles),
            "health": {
                "is_valid": is_healthy,
                "error_count": len(errors),
                "errors": errors[:10],
            },
        }
    )


@sidebar_mgmt_bp.route("/items/<int:item_id>/toggle", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("sidebar_item_toggle")
def toggle_sidebar_item(item_id):
    """Toggle a sidebar item on/off."""
    item = SidebarMenuItem.query.get_or_404(item_id)
    item.is_enabled = not item.is_enabled
    db.session.commit()

    SidebarMenuAuditLog.log_toggle(
        item_key=item.key,
        new_state=item.is_enabled,
        reason=f"Admin toggled {item.label} (via API)"
    )

    logger.info(f"Sidebar item toggled: {item.key} -> {item.is_enabled}")

    return jsonify(
        {
            "status": "success",
            "item": item.to_dict(),
            "message": f"{'Enabled' if item.is_enabled else 'Disabled'}: {item.label}",
        }
    )


@sidebar_mgmt_bp.route("/items/section/<section>/toggle", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("sidebar_section_toggle")
def toggle_section(section):
    """Toggle all items in a section."""
    data = request.get_json() or {}
    enabled = data.get("enabled", None)

    items = SidebarMenuItem.query.filter_by(section=section).all()

    if enabled is None:
        any_disabled = any(not i.is_enabled for i in items)
        enabled = any_disabled

    for item in items:
        item.is_enabled = enabled

    db.session.commit()

    SidebarMenuAuditLog.log_toggle(
        item_key=f"section.{section}",
        new_state=enabled,
        reason=f"Admin toggled entire section {section}"
    )

    logger.info(f"Section toggled: {section} -> {enabled}")

    return jsonify(
        {
            "status": "success",
            "section": section,
            "is_enabled": enabled,
            "item_count": len(items),
            "message": f"{'Enabled' if enabled else 'Disabled'} {len(items)} items in {section}",
        }
    )


@sidebar_mgmt_bp.route("/items/subsection/<section>/<subsection>/toggle", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("sidebar_subsection_toggle")
def toggle_subsection(section, subsection):
    """Toggle all items in a subsection."""
    data = request.get_json() or {}
    enabled = data.get("enabled", None)

    items = SidebarMenuItem.query.filter_by(section=section, subsection=subsection).all()

    if enabled is None:
        any_disabled = any(not i.is_enabled for i in items)
        enabled = any_disabled

    for item in items:
        item.is_enabled = enabled

    db.session.commit()

    logger.info(f"Subsection toggled: {section}/{subsection} -> {enabled}")

    return jsonify(
        {
            "status": "success",
            "section": section,
            "subsection": subsection,
            "is_enabled": enabled,
            "item_count": len(items),
            "message": f"{'Enabled' if enabled else 'Disabled'} {len(items)} items in {section} -> {subsection}",
        }
    )


@sidebar_mgmt_bp.route("/items/reset", methods=["POST"])
@login_required
@platform_admin_required
@audit_log("sidebar_items_reset")
def reset_all_items():
    """Reset all items to enabled state."""
    items = SidebarMenuItem.query.all()
    for item in items:
        item.is_enabled = True
    db.session.commit()

    logger.info("All sidebar items reset to enabled")

    return jsonify({"status": "success", "message": f"Reset {len(items)} items to enabled"})
