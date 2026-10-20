"""
Answer quality figures for one organisation and period.

The citation rate, the citation-gate refusal rate per thousand and the
refusal rate per persona are computed from recorded rows only: answer
records, agent run records and the audit rows the citation gate writes.
Every figure is scoped to the caller's organisation. A period with nothing
to measure returns None -- never a literal, never a "total or 1"
denominator. The prior period is the same length of time immediately
before the chosen one and is computed by the caller with a second call.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app import db
from app.models.answer_record import AnswerRecord
from app.models.agent_run_record import AgentRunRecord
from app.models.ai_chat_audit_log import AIChatAuditLog
from app.models.user import User
from sqlalchemy import func

REFUSAL_STATUSES = ("would_refuse_uncited", "refused_uncited")
# How far an answer record or audit row may be from a run record to be
# treated as that run's drill-through target.
MATCH_WINDOW = timedelta(minutes=5)


def _unique(ids: List[int]) -> List[int]:
    """Deduplicate ids while preserving order."""
    seen: set[int] = set()
    unique: List[int] = []
    for id_ in ids:
        if id_ not in seen:
            seen.add(id_)
            unique.append(id_)
    return unique


def _closest_run_record(user_id: int, timestamp: datetime) -> Optional[int]:
    """Id of the run record closest to *timestamp* for *user_id*, or None.

    A user belongs to one organisation, so matching by user id is already
    organisation-scoped.
    """
    start = timestamp - MATCH_WINDOW
    end = timestamp + MATCH_WINDOW
    run = (
        AgentRunRecord.query.filter(
            AgentRunRecord.user_id == user_id,
            AgentRunRecord.created_at >= start,
            AgentRunRecord.created_at <= end,
        )
        .order_by(
            func.abs(
                func.extract("epoch", AgentRunRecord.created_at)
                - func.extract("epoch", timestamp)
            )
        )
        .limit(1)
        .first()
    )
    return run.id if run else None


def _refusal_logs(org_id: int, start_time: datetime, end_time: datetime) -> List[AIChatAuditLog]:
    """Refusal audit rows for the organisation in the period.

    The audit log has no organisation column of its own, so the fence goes
    through the writing user's organisation.
    """
    return (
        db.session.query(AIChatAuditLog)
        .join(User, AIChatAuditLog.user_id == User.id)
        .filter(
            User.organization_id == org_id,
            AIChatAuditLog.created_at >= start_time,
            AIChatAuditLog.created_at <= end_time,
            AIChatAuditLog.validation_status.in_(REFUSAL_STATUSES),
        )
        .all()
    )


def get_citation_rate(
    org_id: int, start_time: datetime, end_time: datetime
) -> Tuple[Optional[float], List[int]]:
    """Citation rate for the period: cited answers over all answers.

    Returns (rate, uncited_answer_ids). rate is None when the period holds
    no answers for the organisation. uncited_answer_ids are the stored
    answers behind the figure, for drill-through to their addresses.
    """
    answers = (
        AnswerRecord.query.filter(
            AnswerRecord.organization_id == org_id,
            AnswerRecord.computed_at >= start_time,
            AnswerRecord.computed_at <= end_time,
        )
        .order_by(AnswerRecord.computed_at.asc())
        .all()
    )
    if not answers:
        return None, []

    total = len(answers)
    cited = sum(1 for answer in answers if answer.citations)
    uncited_ids = [answer.id for answer in answers if not answer.citations]

    return cited / total, uncited_ids


def get_refusal_rate(
    org_id: int, start_time: datetime, end_time: datetime
) -> Tuple[Optional[float], List[int]]:
    """Refusals per thousand run records for the period.

    Returns (rate, run_ids). rate is None when the period holds no run
    records for the organisation. run_ids are the run records behind the
    refusals, for drill-through to the run-record list.
    """
    runs = (
        AgentRunRecord.query.filter(
            AgentRunRecord.organization_id == org_id,
            AgentRunRecord.created_at >= start_time,
            AgentRunRecord.created_at <= end_time,
        )
        .all()
    )
    if not runs:
        return None, []

    total_runs = len(runs)
    logs = _refusal_logs(org_id, start_time, end_time)
    rate = (len(logs) / total_runs) * 1000.0

    run_ids: List[int] = []
    for log in logs:
        run_id = _closest_run_record(log.user_id, log.created_at)
        if run_id is not None:
            run_ids.append(run_id)

    return rate, _unique(run_ids)


def get_refusal_rate_per_persona(
    org_id: int, start_time: datetime, end_time: datetime
) -> Tuple[Dict[str, Optional[float]], List[int]]:
    """Refusal rate per persona for the period.

    Returns (rates, run_ids). rates maps every persona with a run in the
    period to refusals per thousand, or None when the persona has no runs.
    Personas with refusals but no runs also appear as None. run_ids are the
    run records behind the refusals, for drill-through.
    """
    runs = (
        AgentRunRecord.query.filter(
            AgentRunRecord.organization_id == org_id,
            AgentRunRecord.created_at >= start_time,
            AgentRunRecord.created_at <= end_time,
        )
        .all()
    )

    run_counts: Dict[str, int] = {}
    for record in runs:
        persona = record.persona or "unknown"
        run_counts[persona] = run_counts.get(persona, 0) + 1

    logs = _refusal_logs(org_id, start_time, end_time)
    refusal_counts: Dict[str, int] = {}
    for log in logs:
        persona = log.persona or "unknown"
        refusal_counts[persona] = refusal_counts.get(persona, 0) + 1

    rates: Dict[str, Optional[float]] = {}
    for persona in set(run_counts) | set(refusal_counts):
        run_count = run_counts.get(persona, 0)
        if run_count == 0:
            rates[persona] = None
        else:
            rates[persona] = (refusal_counts.get(persona, 0) / run_count) * 1000.0

    run_ids: List[int] = []
    for log in logs:
        run_id = _closest_run_record(log.user_id, log.created_at)
        if run_id is not None:
            run_ids.append(run_id)

    return rates, _unique(run_ids)


def get_refusal_run_ids_by_persona(
    org_id: int, start_time: datetime, end_time: datetime
) -> Dict[str, List[int]]:
    """Run record ids behind each persona's refusals in the period."""
    by_persona: Dict[str, List[int]] = {}
    for log in _refusal_logs(org_id, start_time, end_time):
        run_id = _closest_run_record(log.user_id, log.created_at)
        if run_id is not None:
            by_persona.setdefault(log.persona or "unknown", []).append(run_id)
    return {persona: _unique(ids) for persona, ids in by_persona.items()}
