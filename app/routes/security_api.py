"""
Security Management API Routes

REST API endpoints for security management, RBAC administration, audit trail access,
and data protection controls.

Endpoints:
- GET /api/security/rbac/permissions - Get user permissions
- POST /api/security/rbac/check - Check specific permission
- GET /api/security/audit/events - Query audit trail
- GET /api/security/audit/integrity - Check audit integrity
- POST /api/security/data/scan - Scan data for PII
- POST /api/security/data/mask - Mask sensitive data
- GET /api/security/status - Get security system status
"""

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from app.middleware.tenant_decorators import platform_admin_required

from app import db
from app.decorators import audit_log
from app.security.audit import audit_logger
from app.security.data_protection import data_protector
from app.security.rbac import Permission, ResourceDomain, check_permission, rbac_manager
from app.utils.pagination import safe_int_arg

logger = logging.getLogger(__name__)

security_bp = Blueprint("security", __name__, url_prefix="/api/security")


@security_bp.route("/rbac/permissions", methods=["GET"])
@login_required
def get_user_permissions():
    """
    Get current user's permissions across all domains.

    Returns:
        User's accessible domains and permissions
    """
    try:
        domains = rbac_manager.get_user_domains(current_user)

        permissions = {}
        for domain in domains:
            domain_perms = []
            for perm in [Permission.READ, Permission.WRITE, Permission.DELETE, Permission.ADMIN]:
                if rbac_manager.check_permission(current_user, domain, perm):
                    domain_perms.append(perm.value)
            permissions[domain.value] = domain_perms

        audit_logger.log_data_access("security", "permissions", "read")

        return jsonify(
            {
                "success": True,
                "data": {
                    "user_id": current_user.id,
                    "user_email": current_user.email,
                    "domains": [d.value for d in domains],
                    "permissions": permissions,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error getting user permissions: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@security_bp.route("/rbac/check", methods=["POST"])
@login_required
@audit_log("security_rbac_check")
def check_user_permission():
    """
    Check if current user has specific permission.

    Request Body:
    {
        "domain": "architecture",
        "permission": "read",
        "resource_id": "optional_resource_id"
    }

    Returns:
        Permission check result
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body required"}), 400

        domain_str = data.get("domain")
        permission_str = data.get("permission")
        resource_id = data.get("resource_id")

        if not domain_str or not permission_str:
            return jsonify({"error": "domain and permission are required"}), 400

        # Validate domain
        try:
            domain = ResourceDomain(domain_str)
        except ValueError:
            return jsonify({"error": f"Invalid domain: {domain_str}"}), 400

        # Validate permission
        try:
            permission = Permission[permission_str.upper()]
        except KeyError:
            return jsonify({"error": f"Invalid permission: {permission_str}"}), 400

        # Check permission
        has_permission = rbac_manager.check_permission(
            current_user, domain, permission, resource_id
        )

        audit_logger.log_authorization(
            domain_str, resource_id or "*", permission_str, has_permission
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "user_id": current_user.id,
                    "domain": domain_str,
                    "permission": permission_str,
                    "resource_id": resource_id,
                    "granted": has_permission,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error checking permission: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@security_bp.route("/audit/events", methods=["GET"])
@login_required
@platform_admin_required
def get_audit_events():
    """
    Query audit trail events.

    Query Parameters:
    - user_id: Filter by user ID
    - resource_type: Filter by resource type
    - event_type: Filter by event type
    - start_date: Start date (ISO format)
    - end_date: End date (ISO format)
    - limit: Maximum number of events (default 100)

    Returns:
        List of audit events
    """
    try:
        # Check if user has audit access
        if not check_permission(ResourceDomain.AUDIT, Permission.READ):
            return jsonify({"error": "Insufficient permissions"}), 403

        # Parse query parameters
        user_id = request.args.get("user_id", type=int)
        resource_type = request.args.get("resource_type")
        event_type = request.args.get("event_type")
        limit = safe_int_arg('limit', 100, minimum=1, maximum=500)

        start_date = None
        end_date = None

        if request.args.get("start_date"):
            try:
                start_date = datetime.fromisoformat(request.args["start_date"])
            except ValueError:
                return jsonify({"error": "Invalid start_date format"}), 400

        if request.args.get("end_date"):
            try:
                end_date = datetime.fromisoformat(request.args["end_date"])
            except ValueError:
                return jsonify({"error": "Invalid end_date format"}), 400

        # Query audit events
        events = audit_logger.get_audit_trail(
            user_id=user_id,
            resource_type=resource_type,
            event_type=event_type,
            start_date=start_date,
            end_date=end_date,
            limit=min(limit, 1000),  # Cap at 1000
        )

        # Convert to dict format
        event_data = [event.to_dict() for event in events]

        audit_logger.log_data_access("audit", "events", "read")

        return jsonify({"success": True, "data": event_data, "count": len(event_data)})

    except Exception as e:
        logger.error(f"Error querying audit events: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@security_bp.route("/audit/integrity", methods=["GET"])
@login_required
def check_audit_integrity():
    """
    Check integrity of audit trail.

    Returns:
        Audit integrity status
    """
    try:
        # Check if user has audit access
        if not check_permission(ResourceDomain.AUDIT, Permission.ADMIN):
            return jsonify({"error": "Insufficient permissions"}), 403

        integrity_result = audit_logger.verify_audit_integrity()

        audit_logger.log_data_access("audit", "integrity", "check")

        return jsonify({"success": True, "data": integrity_result})

    except Exception as e:
        logger.error(f"Error checking audit integrity: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@security_bp.route("/data/scan", methods=["POST"])
@login_required
@audit_log("security_pii_scan")
def scan_data_for_pii():
    """
    Scan data for PII content.

    Request Body:
    {
        "data": "text or object to scan",
        "max_sensitivity": "internal|confidential|restricted"
    }

    Returns:
        PII scan results
    """
    try:
        data = request.get_json()
        if not data or "data" not in data:
            return jsonify({"error": "data field is required"}), 400

        scan_data = data["data"]
        max_sensitivity_str = data.get("max_sensitivity", "internal")

        # Validate sensitivity level
        from app.security.data_protection import DataSensitivity

        try:
            max_sensitivity = DataSensitivity(max_sensitivity_str)
        except ValueError:
            return jsonify({"error": f"Invalid max_sensitivity: {max_sensitivity_str}"}), 400

        # Scan for PII
        findings = data_protector.scan_for_pii(scan_data)
        is_safe = data_protector.is_data_safe(scan_data, max_sensitivity)

        audit_logger.log_data_access("security", "pii_scan", "scan")

        return jsonify(
            {
                "success": True,
                "data": {
                    "findings": findings,
                    "finding_count": len(findings),
                    "is_safe": is_safe,
                    "max_sensitivity": max_sensitivity_str,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error scanning data for PII: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@security_bp.route("/data/mask", methods=["POST"])
@login_required
@audit_log("security_data_mask")
def mask_sensitive_data():
    """
    Mask sensitive data in provided content.

    Request Body:
    {
        "data": "text or object to mask",
        "strategy": "redact|hash|encrypt|partial",
        "sensitivity_level": "internal|confidential|restricted"
    }

    Returns:
        Masked data
    """
    try:
        data = request.get_json()
        if not data or "data" not in data:
            return jsonify({"error": "data field is required"}), 400

        input_data = data["data"]
        strategy_str = data.get("strategy", "redact")
        sensitivity_str = data.get("sensitivity_level", "confidential")

        # Validate strategy
        from app.security.data_protection import DataSensitivity, MaskingStrategy

        try:
            strategy = MaskingStrategy(strategy_str)
        except ValueError:
            return jsonify({"error": f"Invalid strategy: {strategy_str}"}), 400

        try:
            sensitivity = DataSensitivity(sensitivity_str)
        except ValueError:
            return jsonify({"error": f"Invalid sensitivity_level: {sensitivity_str}"}), 400

        # Apply masking
        masked_data = data_protector.protect_data(input_data, sensitivity, strategy)

        audit_logger.log_data_modification(
            "security",
            "data_masking",
            "mask",
            old_values={"original_length": len(str(input_data))},
            new_values={"masked_length": len(str(masked_data))},
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "original": input_data,
                    "masked": masked_data,
                    "strategy": strategy_str,
                    "sensitivity_level": sensitivity_str,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error masking sensitive data: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@security_bp.route("/status", methods=["GET"])
@login_required
def get_security_status():
    """
    Get overall security system status.

    Returns:
        Security system health and statistics
    """
    try:
        # Check if user has security access
        if not check_permission(ResourceDomain.SECURITY, Permission.READ):
            return jsonify({"error": "Insufficient permissions"}), 403

        # Get audit integrity status
        integrity_status = audit_logger.verify_audit_integrity()

        # Get basic security stats
        from app.security.audit import AuditEvent

        # Count events by type in last 24 hours
        yesterday = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        recent_events = (
            db.session.query(AuditEvent.event_type, db.func.count(AuditEvent.id).label("count"))
            .filter(AuditEvent.timestamp >= yesterday)
            .group_by(AuditEvent.event_type)
            .all()
        )

        event_counts = {event.event_type: event.count for event in recent_events}

        audit_logger.log_data_access("security", "status", "read")

        return jsonify(
            {
                "success": True,
                "data": {
                    "audit_integrity": integrity_status,
                    "recent_activity": event_counts,
                    "rbac_enabled": True,
                    "data_protection_enabled": True,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            }
        )

    except Exception as e:
        logger.error(f"Error getting security status: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@security_bp.errorhandler(404)
def security_not_found(error):
    """Handle blueprint-scoped 404 errors."""
    logger.warning(
        "Security API 404 route=%s method=%s: %s",
        request.path,
        request.method,
        error,
    )
    return jsonify({"success": False, "error": "Endpoint not found"}), 404


@security_bp.errorhandler(500)
def security_internal_error(error):
    """Handle blueprint-scoped 500 errors."""
    logger.error(
        "Security API 500 route=%s method=%s: %s",
        request.path,
        request.method,
        error,
        exc_info=True,
    )
    db.session.rollback()
    return jsonify({"success": False, "error": "An internal error occurred"}), 500
