"""Dismissing model-health findings with a recorded reason.

A finding is a false positive only if a person says so and says why. The
decision is stored on the organisation's stored report (``dismissals``) and is
applied when the report is read, so a re-scan that reproduces the same finding
does not bring it back, while a finding that stops being true simply drops out
of the report on its own.
"""
from __future__ import annotations

import copy
import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

MIN_REASON_LENGTH = 3
MAX_REASON_LENGTH = 500


def finding_key(finding: Dict[str, Any]) -> str:
    """Stable identity of a finding: its type plus the elements it concerns."""
    ids = sorted(
        str(e.get("archimate_element_id"))
        for e in finding.get("elements", [])
        if e.get("archimate_element_id") is not None
    )
    return f"{finding.get('type', '')}:{','.join(ids)}"


def clean_reason(raw: Optional[str]) -> Optional[str]:
    """The reason trimmed, or None when it is too short to explain anything."""
    reason = (raw or "").strip()
    if len(reason) < MIN_REASON_LENGTH:
        return None
    return reason[:MAX_REASON_LENGTH]


def split_report(
    report: Dict[str, Any], dismissals: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return (report without dismissed findings, the dismissed findings).

    The summary of the returned report is recomputed from the findings that
    remain, so the counts on the page agree with the list beneath them. Each
    dismissed entry carries the finding, its key and the recorded decision.
    """
    dismissals = dismissals or {}
    open_report = copy.deepcopy(report)
    kept: List[Dict[str, Any]] = []
    dismissed: List[Dict[str, Any]] = []
    for f in open_report.get("findings", []):
        key = finding_key(f)
        if key in dismissals:
            dismissed.append({"key": key, "finding": f, **dismissals[key]})
        else:
            kept.append(f)
    open_report["findings"] = kept
    summary = dict(open_report.get("summary", {}))
    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for f in kept:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
    summary.update(total=len(kept), by_type=by_type, by_severity=by_severity)
    open_report["summary"] = summary
    return open_report, dismissed


def dismiss(stored, key: str, reason: str, who: str) -> bool:
    """Record a dismissal on a stored report. False when the key is not a finding."""
    findings = (stored.report_json or {}).get("findings", [])
    if not any(finding_key(f) == key for f in findings):
        return False
    entries = dict(stored.dismissals or {})
    entries[key] = {
        "reason": reason,
        "by": who,
        "at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
    }
    stored.dismissals = entries
    return True


def restore(stored, key: str) -> bool:
    """Remove a dismissal. False when there was none to remove."""
    entries = dict(stored.dismissals or {})
    if key not in entries:
        return False
    del entries[key]
    stored.dismissals = entries
    return True
