"""
Run record routes — list, detail and replay views for organisation administrators.

Every organisation administrator can view and replay the agent run records for
their organisation. Records from other organisations are never returned.
"""
import logging
import re
from datetime import datetime, timedelta

from flask import abort, jsonify, render_template, request
from flask_login import current_user

from app.models.agent_charter import AgentCharter
from app.models.agent_run_record import AgentRunRecord
from app.models.answer_record import AnswerRecord
from app.modules.ai_chat.services.agent_quality_monitor import (
    get_citation_rate,
    get_refusal_rate,
    get_refusal_rate_per_persona,
    REFUSED,
    WOULD_REFUSE,
    get_refusal_run_ids_by_persona,
)

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
        # Drill-through from the quality monitor: only the listed run records.
        # A supplied filter with no valid id matches nothing.
        ids_param = request.args.get("ids")
        if ids_param is not None:
            id_list = [
                int(tok) for tok in ids_param.split(",")
                if re.fullmatch(r"[0-9]{1,18}", tok.strip())
            ]
            query = query.filter(AgentRunRecord.id.in_(id_list))
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

    @bp.route("/quality-monitor")
    def quality_monitor():
        """Answer quality figures for this organisation: a period against the
        prior period of the same length."""
        _require_org_admin()
        org_id = _org_id()
        if org_id is None:
            abort(400)
        try:
            end_time = (
                datetime.fromisoformat(request.args["end"])
                if request.args.get("end") else datetime.utcnow()
            )
            start_time = (
                datetime.fromisoformat(request.args["start"])
                if request.args.get("start") else end_time - timedelta(days=30)
            )
        except ValueError:
            abort(400)
        if start_time >= end_time:
            abort(400)
        prior_start = start_time - (end_time - start_time)
        prior_end = start_time

        citation_rate, uncited_ids = get_citation_rate(org_id, start_time, end_time)
        prior_citation_rate, _ = get_citation_rate(org_id, prior_start, prior_end)

        def decisions(status):
            rate, run_ids = get_refusal_rate(org_id, start_time, end_time, status)
            prior_rate, _ = get_refusal_rate(org_id, prior_start, prior_end, status)
            persona_rates, _ = get_refusal_rate_per_persona(org_id, start_time, end_time, status)
            prior_persona_rates, _ = get_refusal_rate_per_persona(
                org_id, prior_start, prior_end, status
            )
            return {
                "rate": rate,
                "prior_rate": prior_rate,
                "run_ids": run_ids,
                "persona_rates": persona_rates,
                "prior_persona_rates": prior_persona_rates,
                "persona_run_ids": get_refusal_run_ids_by_persona(
                    org_id, start_time, end_time, status
                ),
            }

        uncited_addresses = []
        if uncited_ids:
            uncited_addresses = [
                a.address
                for a in AnswerRecord.query.filter(
                    AnswerRecord.id.in_(uncited_ids),
                    AnswerRecord.organization_id == org_id,
                ).order_by(AnswerRecord.computed_at.asc())
            ]

        return render_template(
            "ai_chat/run_records/quality_monitor.html",
            start_time=start_time,
            end_time=end_time,
            prior_start=prior_start,
            prior_end=prior_end,
            citation_rate=citation_rate,
            prior_citation_rate=prior_citation_rate,
            uncited_addresses=uncited_addresses,
            refused=decisions(REFUSED),
            would=decisions(WOULD_REFUSE),
        )
