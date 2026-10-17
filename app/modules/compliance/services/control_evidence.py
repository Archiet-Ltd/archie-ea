"""The one rule for whether an adopted control has evidence, and whether it still holds.

Three places used to decide "is evidence missing" with three slightly different
rules (the compliance lens, the risk-propagation answer and the framework
control list), and none of them knew evidence can lapse. They all call
:func:`evidence_state` now.

Pure functions over a mapping row (anything carrying the evidence columns of
``ApplicationComplianceControl``) and over control test rows; no query is made
here, so callers keep control of their own tenant scoping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

NOT_REQUIRED_STATUSES = frozenset({"not_applicable", "waived"})

NOT_REQUIRED = "not_required"
MISSING = "missing"
EXPIRED = "expired"
CURRENT = "current"
UNDATED = "undated"

# States that put a control on the evidence-gap list.
GAP_STATES = frozenset({MISSING, EXPIRED})

EFFECTIVE = "effective"
INEFFECTIVE = "ineffective"
EXCEPTION = "exception"
NOT_TESTED = "not_tested"


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def evidence_state(mapping, as_of: Optional[datetime] = None) -> str:
    """Exactly one of ``not_required``, ``missing``, ``expired``, ``current``, ``undated``.

    * ``not_required``: the control is not applicable or waived.
    * ``missing``: no evidence link and no verification date.
    * ``expired``: evidence is recorded and its validity date is before ``as_of``.
    * ``current``: evidence is recorded and valid until ``as_of`` or later.
    * ``undated``: evidence is recorded with no validity date.
    """
    if (getattr(mapping, "implementation_status", None) or "") in NOT_REQUIRED_STATUSES:
        return NOT_REQUIRED
    return link_state(mapping, as_of)


def link_state(mapping, as_of: Optional[datetime] = None) -> str:
    """The state of the evidence the row carries, whatever the control's applicability.

    One of ``missing``, ``expired``, ``current``, ``undated``. A waived or not-applicable
    control is still holding a link with its own validity date; callers that reuse that
    link for another control (harmonisation) need this, not :func:`evidence_state`.
    """
    has_url = bool((getattr(mapping, "evidence_url", None) or "").strip())
    verified = getattr(mapping, "verified_date", None) is not None
    if not has_url and not verified:
        return MISSING
    valid_until = _naive_utc(getattr(mapping, "evidence_valid_until", None))
    if valid_until is None:
        return UNDATED
    moment = _naive_utc(as_of) if as_of is not None else datetime.utcnow()
    return EXPIRED if valid_until < moment else CURRENT


def has_usable_evidence_link(mapping, as_of: Optional[datetime] = None) -> bool:
    """True when the row carries an evidence link that has not lapsed.

    The framework control list shows the link itself, so it needs the link and the
    rule's verdict on it: a link past its validity date is not usable evidence.
    """
    has_url = bool((getattr(mapping, "evidence_url", None) or "").strip())
    return has_url and link_state(mapping, as_of) != EXPIRED


def effectiveness(tests: Iterable) -> str:
    """The control's effectiveness from its tests: the latest test of each kind decides.

    ``effective`` only when the latest design test and the latest operating test
    are both ``effective``; ``ineffective`` if either latest is ``ineffective``;
    ``exception`` when neither is ineffective but one is an exception (a recorded
    exception shows even before the other kind has been tested); ``not_tested``
    when either kind has no test yet and nothing recorded is a failure or exception.
    """
    latest = {}
    for test in tests:
        kind = test.test_kind
        key = (test.tested_at or datetime.min, test.id or 0)
        if kind not in latest or key > latest[kind][0]:
            latest[kind] = (key, test.result)
    results = {kind: pair[1] for kind, pair in latest.items()}
    if results.get("design") == INEFFECTIVE or results.get("operating") == INEFFECTIVE:
        return INEFFECTIVE
    if EXCEPTION in results.values():
        return EXCEPTION
    if "design" not in results or "operating" not in results:
        return NOT_TESTED
    return EFFECTIVE
