"""The one rule for the evidence on an adopted control, and for its effectiveness.

Every surface that says whether a control has evidence (the compliance lens on
the Ask page, the risk-propagation answer, the adopted-control list and the
evidence-gap list) asks ``evidence_state``; none of them reads the evidence
columns to decide for itself. Likewise ``control_effectiveness`` is the one
reading of a control's tests.

Evidence states, exactly one per adopted control row:

* ``not_required``  the control is not applicable or waived
* ``missing``       no evidence link and no verification date
* ``expired``       evidence recorded, valid until a date that has passed
* ``current``       evidence recorded, valid until a later date
* ``undated``       evidence recorded with no validity date

Timestamps in the register are naive UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

EVIDENCE_NOT_REQUIRED_STATUSES = frozenset({"not_applicable", "waived"})

STATE_NOT_REQUIRED = "not_required"
STATE_MISSING = "missing"
STATE_EXPIRED = "expired"
STATE_CURRENT = "current"
STATE_UNDATED = "undated"

# The states that make a control an evidence gap.
GAP_STATES = frozenset({STATE_MISSING, STATE_EXPIRED})
# The states in which evidence is on record and in date (or undated).
HELD_STATES = frozenset({STATE_CURRENT, STATE_UNDATED})

EFFECTIVE = "effective"
INEFFECTIVE = "ineffective"
EXCEPTION = "exception"
NOT_TESTED = "not_tested"


def naive_utc(moment: Optional[datetime] = None) -> datetime:
    """A moment as naive UTC; now when none is given."""
    if moment is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def evidence_url_recorded(mapping) -> bool:
    """Whether an evidence link is recorded on the row (a blank link is none)."""
    return bool((getattr(mapping, "evidence_url", None) or "").strip())


def evidence_state(mapping, as_of: Optional[datetime] = None) -> str:
    """The evidence state of one adopted control row at ``as_of`` (now by default)."""
    if mapping.implementation_status in EVIDENCE_NOT_REQUIRED_STATUSES:
        return STATE_NOT_REQUIRED
    if not evidence_url_recorded(mapping) and mapping.verified_date is None:
        return STATE_MISSING
    valid_until = getattr(mapping, "evidence_valid_until", None)
    if valid_until is None:
        return STATE_UNDATED
    if valid_until < naive_utc(as_of):
        return STATE_EXPIRED
    return STATE_CURRENT


def latest_tests_by_kind(tests: Iterable) -> dict:
    """The most recent test of each kind (``design`` / ``operating``); ties go to the later row."""
    latest: dict = {}
    for test in tests:
        key = (test.tested_at or datetime.min, test.id or 0)
        held = latest.get(test.test_kind)
        if held is None or key > (held.tested_at or datetime.min, held.id or 0):
            latest[test.test_kind] = test
    return latest


def control_effectiveness(tests: Iterable) -> str:
    """``effective`` only when the latest design and the latest operating test are both
    effective; ``ineffective`` when either latest is; ``not_tested`` when either kind has no
    test; otherwise ``exception`` (a latest test recorded an exception)."""
    latest = latest_tests_by_kind(tests)
    results = {kind: (latest[kind].result if kind in latest else None) for kind in ("design", "operating")}
    if INEFFECTIVE in results.values():
        return INEFFECTIVE
    if None in results.values():
        return NOT_TESTED
    if EXCEPTION in results.values():
        return EXCEPTION
    return EFFECTIVE
