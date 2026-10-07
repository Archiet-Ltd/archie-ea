"""Unusual export volume, judged against each person's own history (R1-B88, PB-0238).

Every file download is one ``export`` row in the audit trail
(``app/middleware/usage_tracking.py``). This service reads those rows, never
writes a second record of them, and decides whether a member's last 24 hours are
out of line with their own previous 28 days. A flag is one entry in the one
approval queue, carrying each export's file name and time, and approving it
reduces the member's access through ``rbac_service.revoke_member_access``.

The rule, in one place:

* history is the 28 days before the last 24 hours, counted in whole days with a
  zero for each day without an export, starting from the later of the member's
  first audit row and 28 days ago; under 7 days of history means no flag;
* threshold = mean + 4 * max(std, sqrt(mean), 1);
* flag when the last-24-hours count is above the threshold AND at least 10 (the
  10 only keeps tiny numbers quiet; it never flags anything by itself).
"""

import logging
import math
from datetime import datetime, timedelta

from app import db
from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)

EXPORT_ACTION = "export"
FLAG_ACTION = "export_spike_flag"
WINDOW = timedelta(hours=24)
HISTORY_DAYS = 28
MIN_HISTORY_DAYS = 7
SIGMA_MULTIPLE = 4
MIN_COUNT = 10
MAX_LISTED_EXPORTS = 200
FLAG_EXPIRY_MINUTES = 7 * 24 * 60
FLAG_OPERATION = "restrict_access"
FLAG_ENTITY = "user"


def is_export_row(row) -> bool:
    """True when the audit row records one file download.

    The one classifier: the scan, the screen and the evidence all count rows
    through this and ``export_clause()`` (its SQL form, kept beside it).
    """
    return row.action == EXPORT_ACTION and row.table_name == "export"


def export_clause():
    """``is_export_row`` as a query condition."""
    return db.and_(AuditLog.action == EXPORT_ACTION, AuditLog.table_name == "export")


def _export_times(org_id, user_id, start, end):
    """Creation times of one member's export rows in ``[start, end)``."""
    rows = (
        db.session.query(AuditLog.created_at)
        .filter(
            AuditLog.org_predicate(org_id),
            AuditLog.user_id == user_id,
            export_clause(),
            AuditLog.created_at >= start,
            AuditLog.created_at < end,
        )
        .all()
    )
    return [r[0] for r in rows]


def _first_audit_at(org_id, user_id):
    return (
        db.session.query(db.func.min(AuditLog.created_at))
        .filter(AuditLog.org_predicate(org_id), AuditLog.user_id == user_id)
        .scalar()
    )


def assess_member(org_id, user_id, *, now=None):
    """One member's last-24-hours export count against their own baseline.

    Returns a dict: ``count_24h``, ``count_7d``, ``history_days``,
    ``enough_history``, ``baseline_mean``, ``baseline_std``, ``threshold``,
    ``window_start``, ``window_end`` and ``flag`` (True when the rule fires).
    """
    now = now or datetime.utcnow()
    window_start = now - WINDOW
    after_now = now + timedelta(seconds=1)
    count_24h = len(_export_times(org_id, user_id, window_start, after_now))
    count_7d = len(_export_times(org_id, user_id, now - timedelta(days=7), after_now))

    earliest = window_start - timedelta(days=HISTORY_DAYS)
    first_audit = _first_audit_at(org_id, user_id)
    history_start = max(earliest, first_audit) if first_audit is not None else window_start
    history_days = max(0, int((window_start - history_start).total_seconds() // 86400))

    result = {
        "count_24h": count_24h,
        "count_7d": count_7d,
        "history_days": history_days,
        "enough_history": history_days >= MIN_HISTORY_DAYS,
        "baseline_mean": None,
        "baseline_std": None,
        "threshold": None,
        "window_start": window_start,
        "window_end": now,
        "flag": False,
    }
    if not result["enough_history"]:
        return result

    span_start = window_start - timedelta(days=history_days)
    daily = [0] * history_days
    for stamp in _export_times(org_id, user_id, span_start, window_start):
        index = int((window_start - stamp).total_seconds() // 86400)
        if 0 <= index < history_days:
            daily[index] += 1
    mean = sum(daily) / history_days
    std = math.sqrt(sum((n - mean) ** 2 for n in daily) / history_days)
    threshold = mean + SIGMA_MULTIPLE * max(std, math.sqrt(mean), 1)
    result.update(
        baseline_mean=mean,
        baseline_std=std,
        threshold=threshold,
        flag=count_24h > threshold and count_24h >= MIN_COUNT,
    )
    return result


def _recent_exports(org_id, user_id, window_start, now):
    rows = (
        AuditLog.query.filter(
            AuditLog.org_predicate(org_id),
            AuditLog.user_id == user_id,
            export_clause(),
            AuditLog.created_at >= window_start,
            AuditLog.created_at <= now,
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(MAX_LISTED_EXPORTS)
        .all()
    )
    listed = []
    for row in rows:
        extra = row.extra_json if isinstance(row.extra_json, dict) else {}
        listed.append(
            {
                "audit_id": row.id,
                "at": row.created_at.isoformat(),
                "endpoint": extra.get("endpoint"),
                "filename": extra.get("filename"),
                "bytes": extra.get("bytes"),
            }
        )
    return rows, listed


def _flag_already_raised(org_id, user_id, window_start, newest_audit_id):
    """True when this member has an open flag, or one raised inside the window
    (including a dismissed one: dismissing means the activity was expected), or
    one already built on the same newest export."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    base = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org_id,
        AIChatCRUDApproval.operation_type == FLAG_OPERATION,
        AIChatCRUDApproval.entity_type == FLAG_ENTITY,
        AIChatCRUDApproval.entity_id == user_id,
    )
    if base.filter(AIChatCRUDApproval.status == ApprovalStatus.PENDING).first() is not None:
        return True
    if base.filter(AIChatCRUDApproval.created_at > window_start).first() is not None:
        return True
    same_source = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org_id,
        AIChatCRUDApproval.source_table == AuditLog.__tablename__,
        AIChatCRUDApproval.source_id == newest_audit_id,
    ).first()
    return same_source is not None


def scan_organisation(org_id, *, now=None):
    """Flag every member of ``org_id`` whose last 24 hours of exports is out of
    line with their own history. Returns the list of approval ids created."""
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    now = now or datetime.utcnow()
    window_start = now - WINDOW
    member_ids = [
        r[0]
        for r in db.session.query(AuditLog.user_id)
        .filter(
            AuditLog.org_predicate(org_id),
            export_clause(),
            AuditLog.user_id.isnot(None),
            AuditLog.created_at >= window_start,
            AuditLog.created_at <= now,
        )
        .distinct()
        .all()
    ]
    created = []
    for user_id in sorted(member_ids):
        # tenant-scoping-ok: User is not TenantMixin; organization_id is in the predicate.
        member = User.query.filter(User.id == user_id, User.organization_id == org_id).first()
        if member is None:
            continue
        verdict = assess_member(org_id, user_id, now=now)
        if not verdict["flag"]:
            continue
        rows, listed = _recent_exports(org_id, user_id, window_start, now)
        if not rows:
            continue
        newest_id = rows[0].id
        if _flag_already_raised(org_id, user_id, window_start, newest_id):
            continue

        approval = create_approval_record(
            organization_id=org_id,
            operation_type=FLAG_OPERATION,
            entity_type=FLAG_ENTITY,
            entity_id=user_id,
            summary="%s downloaded %d files in the last 24 hours; usually about %d a day."
            % (member.full_name(), verdict["count_24h"], round(verdict["baseline_mean"])),
            operation_payload={
                "exports": listed,
                "count_24h": verdict["count_24h"],
                "baseline_mean": round(verdict["baseline_mean"], 2),
                "baseline_std": round(verdict["baseline_std"], 2),
                "threshold": round(verdict["threshold"], 2),
                "window_start": window_start.isoformat(),
                "window_end": now.isoformat(),
            },
            source_table=AuditLog.__tablename__,
            source_id=newest_id,
            expiry_minutes=FLAG_EXPIRY_MINUTES,
        )
        db.session.commit()
        created.append(approval.id)
        AuditLog.log(
            action=FLAG_ACTION,
            table_name="ai_chat_crud_approvals",
            record_id=approval.id,
            organization_id=org_id,
            user_id=None,
            extra_json={
                "member_id": user_id,
                "count_24h": verdict["count_24h"],
                "threshold": round(verdict["threshold"], 2),
            },
        )
    return created


def member_activity(org_id, *, now=None):
    """Members with exports in the last 7 days, busiest first, each with the
    verdict from ``assess_member`` (for the investigation screen)."""
    now = now or datetime.utcnow()
    since = now - timedelta(days=7)
    ids = [
        r[0]
        for r in db.session.query(AuditLog.user_id)
        .filter(
            AuditLog.org_predicate(org_id),
            export_clause(),
            AuditLog.user_id.isnot(None),
            AuditLog.created_at >= since,
        )
        .distinct()
        .all()
    ]
    rows = []
    for user_id in ids:
        # tenant-scoping-ok: User is not TenantMixin; organization_id is in the predicate.
        member = User.query.filter(User.id == user_id, User.organization_id == org_id).first()
        if member is None:
            continue
        verdict = assess_member(org_id, user_id, now=now)
        verdict["user"] = member
        rows.append(verdict)
    rows.sort(key=lambda r: (-r["count_24h"], -r["count_7d"]))
    return rows


def open_flags(org_id):
    """Pending export flags of ``org_id``, newest first."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    return (
        AIChatCRUDApproval.query.filter(
            AIChatCRUDApproval.organization_id == org_id,
            AIChatCRUDApproval.operation_type == FLAG_OPERATION,
            AIChatCRUDApproval.entity_type == FLAG_ENTITY,
            AIChatCRUDApproval.status == ApprovalStatus.PENDING,
        )
        .order_by(AIChatCRUDApproval.id.desc())
        .all()
    )
