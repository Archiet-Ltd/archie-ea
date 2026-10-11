"""One write path for recording a capability's maturity, with history.

Three code paths used to update the maturity fields of ``business_capability``
by hand — the edit route, the batch route and the assistant tool — and none of
them wrote to the history table at all. This module owns the single write path:

``record_capability_maturity`` updates the source row (current level, target
level, gap, assessment date, notes) and appends exactly one history row on
``capability_maturity_assessment`` in the same transaction. Every writer that
updates an existing capability's maturity calls it; nothing else writes those
columns.

The write is explicitly scoped by ``organization_id`` so the function can run
inside a loop (the batch route) without per-call commits, and so a caller
outside a request context cannot accidentally write another organisation's
row. A capability that does not belong to the organisation reads back as not
found, with no information about whether it exists elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.capability_models import CapabilityMaturityAssessment


class MaturityValidationError(ValueError):
    """A maturity level was provided outside the valid 1-5 range."""


class CapabilityNotFoundError(LookupError):
    """No capability with this id belongs to the given organisation.

    Deliberately the same outcome for "does not exist" and "belongs to
    another organisation": a caller must not be able to discover rows in
    other tenants.
    """


def _naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate_level(value, label: str) -> None:
    """A real level must be an integer in 1..5; None means "not recorded"."""
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= 5):
        raise MaturityValidationError(f"{label} maturity level must be between 1 and 5")


def record_capability_maturity(
    *,
    capability_id: int,
    organization_id: int,
    current: int | None,
    target: int | None = None,
    notes: str | None = None,
    assessor_user_id: int | None = None,
    assessor_name: str | None = None,
    assessor_role: str | None = None,
    clear_current: bool = False,
    clear_target: bool = False,
    commit: bool = True,
) -> BusinessCapability:
    """Record a maturity assessment and append one history row.

    Updates the source ``business_capability`` row and appends exactly one
    ``capability_maturity_assessment`` row, in the same transaction.

    Args:
        capability_id: The source row to record against.
        organization_id: The organisation that owns the capability. Required
            explicitly so the write can never cross organisations and so the
            function works when called in a loop without a per-call commit.
        current: The recorded current level, 1-5, or None when no current
            level is being recorded.
        target: The recorded target level, 1-5, or None.
        notes: Free-text assessment notes; None leaves the stored notes alone.
        assessor_user_id: The acting user's id (kept for the caller's audit
            trail; the history table has no user-id column).
        assessor_name: The acting user's display name, stored on the history
            row.
        assessor_role: The acting user's role, stored on the history row.
        clear_current: Clear the stored current level (set it to not
            recorded). Cannot be combined with ``current``. The clearing is
            written into the notes and the history row.
        clear_target: Clear the stored target level, with the same trace.
        commit: When True, commits the transaction. Pass False when batching:
            the caller commits once after the loop.

    Raises:
        MaturityValidationError: a level outside 1-5 was provided.
        CapabilityNotFoundError: no capability with this id belongs to the
            organisation (same answer whether it does not exist or belongs to
            another organisation).
    """
    # The history table stores a level (NOT NULL) — validate before writing.
    _validate_level(current, "current")
    _validate_level(target, "target")
    if clear_current and current is not None:
        raise MaturityValidationError(
            "current maturity level cannot be both set and cleared"
        )
    if clear_target and target is not None:
        raise MaturityValidationError(
            "target maturity level cannot be both set and cleared"
        )

    if not isinstance(organization_id, int) or isinstance(organization_id, bool):
        raise CapabilityNotFoundError(
            f"Business capability {capability_id} not found."
        )

    capability = BusinessCapability.query.filter_by(
        id=capability_id, organization_id=organization_id
    ).first()
    if capability is None:
        raise CapabilityNotFoundError(
            f"Business capability {capability_id} not found."
        )

    previous_current = capability.current_maturity_level
    clearing: list[str] = []
    if clear_current and previous_current is not None:
        capability.current_maturity_level = None
        clearing.append(f"Current maturity level cleared (was {previous_current}).")
    elif current is not None:
        capability.current_maturity_level = current
    if clear_target and capability.target_maturity_level is not None:
        clearing.append(
            f"Target maturity level cleared (was {capability.target_maturity_level})."
        )
        capability.target_maturity_level = None
    elif target is not None:
        capability.target_maturity_level = target

    # A clearing leaves a trace in the notes, appended to whatever notes the
    # caller supplied (or to the stored notes when none were supplied).
    if clearing:
        base = notes if notes is not None else (capability.maturity_assessment_notes or "")
        notes = (base + "\n" if base else "") + " ".join(clearing)
    if notes is not None:
        capability.maturity_assessment_notes = notes

    # The gap is recomputed only when both ends are real — never substituted
    # with 0 (the old route default) when one end is missing.
    if (
        capability.current_maturity_level is not None
        and capability.target_maturity_level is not None
    ):
        capability.maturity_gap = (
            capability.target_maturity_level - capability.current_maturity_level
        )
    else:
        capability.maturity_gap = None

    now = _naive_utcnow()
    capability.updated_at = now

    # Determine the level for the history row. When a current level is provided
    # in the call, use it. When only target or notes change but the source row
    # already holds a level, record that level in history too so every
    # assessment event (target change, notes update) leaves a trace. Skip
    # entirely only when neither argument nor stored value has a current level.
    level_for_history = (
        current
        if current is not None
        else capability.current_maturity_level
        if capability.current_maturity_level is not None
        else previous_current
    )
    history: CapabilityMaturityAssessment | None = None
    if level_for_history is not None:
        capability.maturity_assessment_date = now
        history = CapabilityMaturityAssessment(
            organization_id=organization_id,
            business_capability_id=capability.id,
            assessment_date=now.date(),
            assessor_name=assessor_name,
            assessor_role=assessor_role,
            maturity_level=level_for_history,
            target_maturity_level=capability.target_maturity_level,
            maturity_framework="custom",
            notes=notes,
        )
        db.session.add(history)

    if commit:
        db.session.commit()
    else:
        db.session.flush()

    return capability