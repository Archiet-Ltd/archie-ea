"""
Answer quality figures for one organisation and period.

The citation rate, the citation-gate refusal rate per thousand and the
refusal rate per persona are computed from recorded rows only: answer
records, agent run records and the audit rows the citation gate writes.
Every figure is scoped to the caller's organisation. A period with nothing
to measure returns None -- never a literal, never a "total or 1"
denominator. The prior period is the same length of time immediately
before the chosen one and is computed by the caller with a second call.

A period includes its start and excludes its end, so a row exactly on the
boundary between a period and the prior one is counted once.

Refusals that happened ("refused") and replies the gate would have refused
("would have refused") are separate figures and are never added together.
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

REFUSED = "refused_uncited"
WOULD_REFUSE = "would_refuse_uncited"
REFUSAL_STATUSES = (WOULD_REFUSE, REFUSED)
# How far an audit row may be from a run record to be treated as that run's
# drill-through target.
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


def _check_status(status: str) -> None:
    if status not in REFUSAL_STATUSES:
        raise ValueError(f"unknown refusal status: {status!r}")


def _runs(org_id: int, start_time: datetime, end_time: datetime) -> List[AgentRunRecord]:
    return (
        AgentRunRecord.query.filter(
            AgentRunRecord.organization_id == org_id,
            AgentRunRecord.created_at >= start_time,
            AgentRunRecord.created_at < end_time,
        )
        .all()
    )


def _closest_run_record(org_id: int, user_id: int, timestamp: datetime) -> Optional[int]:
    """Id of the run record of *org_id* closest to *timestamp* for *user_id*,
    within the match window, or None. The organisation is part of the match so
    a user who moved organisation cannot pull a run from the old one."""
    run = (
        AgentRunRecord.query.filter(
            AgentRunRecord.organization_id == org_id,
            AgentRunRecord.user_id == user_id,
            AgentRunRecord.created_at >= timestamp - MATCH_WINDOW,
            AgentRunRecord.created_at <= timestamp + MATCH_WINDOW,
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


def _refusal_logs(
    org_id: int, start_time: datetime, end_time: datetime, status: str
) -> List[AIChatAuditLog]:
    """Audit rows of one refusal status for the organisation in the period.

    The audit log has no organisation column of its own, so the fence goes
    through the writing user's current organisation.
    """
    return (
        db.session.query(AIChatAuditLog)
        .join(User, AIChatAuditLog.user_id == User.id)
        .filter(
            User.organization_id == org_id,
            AIChatAuditLog.created_at >= start_time,
            AIChatAuditLog.created_at < end_time,
            AIChatAuditLog.validation_status == status,
        )
        .all()
    )


def _run_ids(org_id: int, logs: List[AIChatAuditLog]) -> List[int]:
    ids: List[int] = []
    for log in logs:
        run_id = _closest_run_record(org_id, log.user_id, log.created_at)
        if run_id is not None:
            ids.append(run_id)
    return _unique(ids)


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
            AnswerRecord.computed_at < end_time,
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
    org_id: int, start_time: datetime, end_time: datetime, status: str
) -> Tuple[Optional[float], List[int]]:
    """Gate decisions of one status ("refused_uncited" or
    "would_refuse_uncited") per thousand run records for the period.

    Returns (rate, run_ids). rate is None when the period holds no run
    records for the organisation. run_ids are the run records behind the
    decisions, for drill-through to the run-record list.
    """
    _check_status(status)
    runs = _runs(org_id, start_time, end_time)
    if not runs:
        return None, []
    logs = _refusal_logs(org_id, start_time, end_time, status)
    return (len(logs) / len(runs)) * 1000.0, _run_ids(org_id, logs)


def get_refusal_rate_per_persona(
    org_id: int, start_time: datetime, end_time: datetime, status: str
) -> Tuple[Dict[str, Optional[float]], List[int]]:
    """Gate decisions of one status per thousand run records, per persona.

    Returns (rates, run_ids). rates maps every persona with a run in the
    period to its rate, or None when a persona has decisions but no runs.
    """
    _check_status(status)
    run_counts: Dict[str, int] = {}
    for record in _runs(org_id, start_time, end_time):
        persona = record.persona or "unknown"
        run_counts[persona] = run_counts.get(persona, 0) + 1

    logs = _refusal_logs(org_id, start_time, end_time, status)
    decision_counts: Dict[str, int] = {}
    for log in logs:
        persona = log.persona or "unknown"
        decision_counts[persona] = decision_counts.get(persona, 0) + 1

    rates: Dict[str, Optional[float]] = {}
    for persona in set(run_counts) | set(decision_counts):
        run_count = run_counts.get(persona, 0)
        if run_count == 0:
            rates[persona] = None
        else:
            rates[persona] = (decision_counts.get(persona, 0) / run_count) * 1000.0

    return rates, _run_ids(org_id, logs)


def get_refusal_run_ids_by_persona(
    org_id: int, start_time: datetime, end_time: datetime, status: str
) -> Dict[str, List[int]]:
    """Run record ids behind each persona's decisions of one status."""
    _check_status(status)
    by_persona: Dict[str, List[int]] = {}
    for log in _refusal_logs(org_id, start_time, end_time, status):
        run_id = _closest_run_record(org_id, log.user_id, log.created_at)
        if run_id is not None:
            by_persona.setdefault(log.persona or "unknown", []).append(run_id)
    return {persona: _unique(ids) for persona, ids in by_persona.items()}
