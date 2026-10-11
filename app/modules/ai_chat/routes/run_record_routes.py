"""
Run record routes — list, detail and replay views for organisation administrators.

Every organisation administrator can view and replay the agent run records for
their organisation. Records from other organisations are never returned.
"""
import logging

from flask import abort, jsonify, render_template, request
from flask_login import current_user

from app.models.agent_charter import AgentCharter
from app.models.agent_run_record import AgentRunRecord

logger = logging.getLogger(__name__)


def _org_id():
    """Return the current user's organisation id, or None."""
    if not current_user or not current_user.is_authenticated:
        return None
    return getattr(current_user, "organization_id", None)


def _require_org_admin():
    """Abort 403 if the current user is not an organisation admin."""
    if not current_user or not current_user.is_authenticated:
        abort(401)
    if not getattr(current_user, "is_org_admin", False) and \
       not getattr(current_user, "is_platform_admin", False):
        abort(403)


def register_run_record_routes(bp):
    """Register run-record routes on a Flask Blueprint."""

    @bp.route("/run-records")
    def run_record_list():
        """List agent run records for this organisation."""
        _require_org_admin()
        org_id = _org_id()
        if org_id is None:
            abort(400)
        page = request.args.get("page", 1, type=int)
        per_page = 20
        query = AgentRunRecord.query.filter_by(organization_id=org_id).order_by(
            AgentRunRecord.created_at.desc()
        )
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        records = pagination.items
        return render_template(
            "ai_chat/run_records/list.html",
            records=records,
            pagination=pagination,
        )

    @bp.route("/run-records/<int:record_id>")
    def run_record_detail(record_id):
        """Show one agent run record with its tools called and records read."""
        _require_org_admin()
        org_id = _org_id()
        if org_id is None:
            abort(400)
        record = AgentRunRecord.query.filter_by(
            id=record_id, organization_id=org_id
        ).first_or_404()
        # Resolve the charter used for this run (if any)
        charter = None
        if record.persona and record.charter_version:
            charter = AgentCharter.query.filter_by(
                persona=record.persona,
                version=record.charter_version,
                organization_id=org_id,
            ).first()
        return render_template(
            "ai_chat/run_records/detail.html",
            record=record,
            charter=charter,
        )

    @bp.route("/run-records/<int:record_id>/replay", methods=["POST"])
    def run_record_replay(record_id):
        """Replay the recorded tool calls read-only for this run record.

        Reads the stored ``tools_called`` from the run record and re-issues
        each as a read-only ToolCall through ToolExecutor. Only read-class
        tools are replayed; any tool with a risk_class other than ``read`` is
        skipped with a note in the result. The replay is scoped to the
        record's organisation — cross-organisation replay is refused.
        """
        _require_org_admin()
        org_id = _org_id()
        if org_id is None:
            abort(400)
        record = AgentRunRecord.query.filter_by(
            id=record_id, organization_id=org_id
        ).first_or_404()

        tools_called = record.tools_called or []
        if not tools_called:
            return jsonify({"replayed": [], "message": "No tool calls to replay"})

        from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
        from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

        executor = ToolExecutor(current_user.id, persona=record.persona)
        results = []
        for entry in tools_called:
            tool_name = entry.get("tool") if isinstance(entry, dict) else entry
            arguments = entry.get("arguments", {}) if isinstance(entry, dict) else {}
            schema = TOOL_SCHEMA_BY_NAME.get(tool_name, {})
            risk_class = schema.get("risk_class")
            if risk_class != "read":
                results.append({
                    "tool": tool_name,
                    "replayed": False,
                    "reason": f"Tool risk_class is '{risk_class}', not 'read' — skipped",
                })
                continue
            tc = ToolCall(id=f"replay-{record_id}-{tool_name}", name=tool_name, arguments=arguments)
            result = executor.execute(tc)
            results.append({
                "tool": tool_name,
                "replayed": True,
                "result": result,
            })

        return jsonify({"replayed": results, "record_id": record_id})