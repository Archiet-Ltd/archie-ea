"""Security finding register: record, link a fix, schedule and record a re-test, publish.

The one place that writes ``SecurityFinding``. Every change is also written to
the audit trail (``AuditLog``) against the acting user's organisation, so who
changed what and when is answered by the one audit store.
"""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlparse

from app import db
from app.models.audit_log import AuditLog
from app.models.security_finding import (
    SEVERITIES,
    SEVERITY_LABELS,
    SOURCE_LABELS,
    SOURCES,
    STATUSES,
    SecurityFinding,
)

_SEVERITY_ORDER = {name: index for index, name in enumerate(SEVERITIES)}


class FindingError(ValueError):
    """The requested change is not allowed; the message is shown to the person."""


def _parse_date(value, label, required=False):
    text = (value or "").strip()
    if not text:
        if required:
            raise FindingError(f"{label} is required.")
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise FindingError(f"{label} must be a date in the form YYYY-MM-DD.") from None


def _clean_url(value):
    text = (value or "").strip()
    if not text:
        raise FindingError("A link to the fix pull request is required.")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(text) > 500:
        raise FindingError("The fix link must be a web address starting with http:// or https://.")
    return text


def _audit(finding, action, user, detail):
    AuditLog.log(
        action=action,
        table_name="security_findings",
        record_id=finding.id,
        organization_id=getattr(user, "organization_id", None),
        user_id=getattr(user, "id", None),
        new_value=detail,
    )


def get_finding(finding_id):
    return db.session.get(SecurityFinding, finding_id)


def record_finding(form, user):
    title = (form.get("title") or "").strip()
    if not title:
        raise FindingError("A title is required.")
    if len(title) > 300:
        raise FindingError("The title must be 300 characters or fewer.")
    severity = (form.get("severity") or "").strip().lower()
    if severity not in SEVERITIES:
        raise FindingError("Choose a severity.")
    source = (form.get("source") or "").strip().lower()
    if source not in SOURCES:
        raise FindingError("Choose the source of the finding.")
    finding = SecurityFinding(
        title=title,
        description=(form.get("description") or "").strip() or None,
        severity=severity,
        source=source,
        source_reference=((form.get("source_reference") or "").strip()[:300]) or None,
        discovered_on=_parse_date(form.get("discovered_on"), "Discovery date", required=True),
        status="open",
        created_by_user_id=getattr(user, "id", None),
    )
    db.session.add(finding)
    db.session.flush()
    _audit(finding, "finding_record", user, {"severity": severity, "source": source})
    db.session.commit()
    return finding


def link_fix(finding, fix_pr_url, user):
    if finding.status == "closed":
        raise FindingError("A closed finding cannot take a new fix.")
    finding.fix_pr_url = _clean_url(fix_pr_url)
    finding.status = "fixed_unretested"
    finding.retested_on = None
    finding.retest_result = None
    _audit(finding, "finding_fix", user, {"fix_pr_url": finding.fix_pr_url})
    db.session.commit()
    return finding


def schedule_retest(finding, scheduled_on, user):
    if finding.status == "closed":
        raise FindingError("A closed finding has already been re-tested.")
    finding.retest_scheduled_on = _parse_date(scheduled_on, "Re-test date", required=True)
    _audit(finding, "finding_schedule", user, {"retest_scheduled_on": finding.retest_scheduled_on.isoformat()})
    db.session.commit()
    return finding


def record_retest(finding, passed, retested_on, user):
    """A passing re-test closes the finding; a failing one reopens it."""
    if finding.status != "fixed_unretested" or not finding.fix_pr_url:
        raise FindingError("Link the fix before recording a re-test.")
    when = _parse_date(retested_on, "Re-test date", required=True)
    if when < finding.discovered_on:
        raise FindingError("The re-test cannot be earlier than the discovery date.")
    finding.retested_on = when
    if passed:
        finding.retest_result = "passed"
        finding.status = "closed"
        finding.closed_on = when
    else:
        finding.retest_result = "failed"
        finding.status = "open"
        finding.closed_on = None
    _audit(finding, "finding_retest", user, {"result": finding.retest_result, "retested_on": when.isoformat()})
    db.session.commit()
    return finding


def set_published(finding, publish, user):
    if publish and finding.status != "closed":
        raise FindingError("Only a finding closed after a passing re-test can be published.")
    finding.published_at = datetime.utcnow() if publish else None
    _audit(finding, "finding_publish" if publish else "finding_unpublish", user, {})
    db.session.commit()
    return finding


def _sorted(findings):
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.discovered_on or date.min, f.id),
    )


def tracker_state():
    """Everything the tracker page shows. Counts appear only for what exists."""
    findings = _sorted(db.session.execute(db.select(SecurityFinding)).scalars().all())
    status_counts = {}
    for finding in findings:
        status_counts[finding.status] = status_counts.get(finding.status, 0) + 1
    return {
        "findings": findings,
        "total": len(findings),
        "status_counts": {s: status_counts[s] for s in STATUSES if s in status_counts},
        "unretested": [f for f in findings if f.fixed_without_retest],
    }


def published_summary():
    """The published closed-findings summary: only closed, published findings."""
    rows = (
        db.session.execute(
            db.select(SecurityFinding)
            .where(SecurityFinding.status == "closed")
            .where(SecurityFinding.published_at.is_not(None))
        )
        .scalars()
        .all()
    )
    rows = _sorted(rows)
    by_severity = {}
    by_source = {}
    for row in rows:
        by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
        by_source[row.source] = by_source.get(row.source, 0) + 1
    return {
        "findings": rows,
        "total": len(rows),
        "by_severity": [(SEVERITY_LABELS[s], by_severity[s]) for s in SEVERITIES if s in by_severity],
        "by_source": [
            (SOURCE_LABELS[s], by_source.get(s)) for s in SOURCES
        ],
    }
