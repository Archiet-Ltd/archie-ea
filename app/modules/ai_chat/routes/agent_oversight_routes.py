"""
Agent Oversight Routes

Routes for:
- Pause/Resume all agent writes (organisation admin)
- Refused call log view (organisation admin)
- Tool catalogue export (security architect / platform admin)
- Tool classification check (organisation admin)
"""

from flask import jsonify, request
from flask_login import current_user, login_required

from app.modules.ai_chat.services.agent_oversight_service import AgentOversightService

from . import unified_ai_chat_bp


def _oversight_service():
    return AgentOversightService(current_user.id)


def _require_org_admin():
    """Check if current user is an organisation admin."""
    if not current_user.is_org_admin:
        return jsonify({"success": False, "error": "Organisation administrator privileges required"}), 403
    return None


def _require_security_architect_or_platform_admin():
    """Check if current user is a security architect or platform admin."""
    if not (current_user.has_role("security_architect") or current_user.is_platform_admin):
        return jsonify({"success": False, "error": "Security Architect or Platform Admin role required"}), 403
    return None


# ------------------------------------------------------------------ #
# Oversight State (Pause/Resume)                                     #
# ------------------------------------------------------------------ #

@unified_ai_chat_bp.route("/oversight/state", methods=["GET"])
@login_required
def get_oversight_state():
    """Get the current oversight state for the organisation."""
    err = _require_org_admin()
    if err:
        return err
    result = _oversight_service().get_oversight_state()
    return jsonify(result)


@unified_ai_chat_bp.route("/oversight/pause", methods=["POST"])
@login_required
def pause_all_writes():
    """Activate the stop-all-writes switch for the organisation."""
    err = _require_org_admin()
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason", "").strip()
    if not reason:
        return jsonify({"success": False, "error": "Reason is required"}), 400

    result = _oversight_service().pause_all_writes(reason)
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@unified_ai_chat_bp.route("/oversight/resume", methods=["POST"])
@login_required
def resume_all_writes():
    """Deactivate the stop-all-writes switch for the organisation."""
    err = _require_org_admin()
    if err:
        return err

    result = _oversight_service().resume_all_writes()
    status = 200 if result.get("success") else 400
    return jsonify(result), status


# ------------------------------------------------------------------ #
# Refused Call Log                                                   #
# ------------------------------------------------------------------ #

@unified_ai_chat_bp.route("/oversight/refused-calls", methods=["GET"])
@login_required
def get_refused_calls():
    """Get refused tool calls from the audit log."""
    err = _require_org_admin()
    if err:
        return err

    user_id = request.args.get("user_id", type=int)
    tool_name = request.args.get("tool_name")
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    if limit < 1 or offset < 0:
        return jsonify({"success": False, "error": "limit must be >= 1 and offset must be >= 0"}), 400
    limit = min(limit, 500)

    result = _oversight_service().get_refused_calls(
        user_id=user_id,
        tool_name=tool_name,
        limit=limit,
        offset=offset,
    )
    return jsonify(result)


# ------------------------------------------------------------------ #
# Tool Catalogue Export                                              #
# ------------------------------------------------------------------ #

@unified_ai_chat_bp.route("/oversight/catalogue/export", methods=["GET"])
@login_required
def export_tool_catalogue():
    """Export the complete tool catalogue with risk class and record types."""
    err = _require_security_architect_or_platform_admin()
    if err:
        return err

    write_tools_only = request.args.get("write_tools_only", "false").lower() == "true"
    result = _oversight_service().export_tool_catalogue(write_tools_only=write_tools_only)
    return jsonify(result)


# ------------------------------------------------------------------ #
# Tool Classification Check                                          #
# ------------------------------------------------------------------ #

@unified_ai_chat_bp.route("/oversight/classification/check/<tool_name>", methods=["GET"])
@login_required
def check_tool_classification(tool_name):
    """Check a tool's classification against its actual test touches."""
    err = _require_org_admin()
    if err:
        return err

    result = _oversight_service().check_tool_classification(tool_name)
    return jsonify(result)


@unified_ai_chat_bp.route("/oversight/classification/status", methods=["GET"])
@login_required
def get_all_classification_status():
    """Get classification status for all tools."""
    err = _require_org_admin()
    if err:
        return err

    result = _oversight_service().get_all_classification_status()
    return jsonify(result)


__all__ = [
    "get_oversight_state",
    "pause_all_writes",
    "resume_all_writes",
    "get_refused_calls",
    "export_tool_catalogue",
    "check_tool_classification",
    "get_all_classification_status",
]