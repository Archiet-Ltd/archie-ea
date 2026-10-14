"""AI design conformance review (R1-B55, TB-0338, PB-0235).

A submitted AI design is checked against the approved pattern list, the
fencing rule for untrusted input and the oversight requirements; deviations
are listed, never silently passed; the Architecture Review Board records its
decision as an ``ArchitectureDecision`` row linked to the AI system.

Reuse
-----
* The decision record is the existing ``ArchitectureDecision`` (nullable
  ``ai_system_id`` and ``ai_design_review`` columns added to it); numbering is
  its own ``next_decision_id``; the row lists in the decision register and
  attaches to the element through ``archimate_element_ids`` like any other.
* The oversight checks read the AI system's own recorded profile
  (``ai_system_profile.profile_from_element``) and its flags (``risk_flags``,
  ``HIGH_AUTONOMY_LEVELS``); the unrecorded-value cases the flags skip on
  purpose are listed here as deviations, because a design review must not pass
  what was never recorded.
* The fencing rule is a declared yes or no per design. The content fence module
  (R1-B53's ``context_fence.py``) is not on main yet, so there is nothing to
  call; when it lands, ``fencing_deviations`` is the one place that changes.
* Separation of duties follows ``ARBGovernanceService``: the person who
  submitted a design cannot decide it.

Organisation scoping is explicit: every read names ``organization_id`` and
returns nothing when it is ``None``; every write refuses when it is ``None``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import update

from app import db
from app.models.architecture_decision import ArchitectureDecision
from app.modules.ai_chat.services.ai_evaluation_service import scoped_ai_system

# The approved AI design patterns. A design names the pattern it follows; one
# that names none, or one not on this list, is a deviation. (An organisation-
# editable pattern library is not built; this is the platform's baseline list.)
APPROVED_PATTERNS = (
    "retrieval-grounded-assistant",
    "human-in-the-loop-copilot",
    "reviewed-batch-classifier",
    "rules-with-model-scoring",
)

STATUS_SUBMITTED = "under_review"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
OPEN_STATUSES = ("proposed", STATUS_SUBMITTED)


class DesignReviewError(ValueError):
    """A submission or decision was refused; the message is shown to the person."""


def _deviation(area: str, code: str, message: str) -> dict:
    return {"area": area, "code": code, "message": message}


def pattern_deviations(declaration: dict) -> list:
    pattern = (declaration.get("pattern") or "").strip()
    if not pattern:
        return [_deviation("pattern", "pattern-not-declared",
                           "The design names no pattern from the approved pattern list.")]
    if pattern not in APPROVED_PATTERNS:
        return [_deviation("pattern", "pattern-not-approved",
                           f"The pattern '{pattern}' is not on the approved pattern list "
                           f"({', '.join(APPROVED_PATTERNS)}).")]
    return []


def fencing_deviations(declaration: dict) -> list:
    ingests = declaration.get("ingests_untrusted_content")
    fenced = declaration.get("untrusted_input_fenced")
    if ingests is None:
        return [_deviation("fencing", "fencing-not-declared",
                           "The design does not say whether it takes in retrieved or pasted "
                           "content, so the fencing rule cannot be checked.")]
    if ingests is True and fenced is not True:
        return [_deviation("fencing", "unfenced-untrusted-input",
                           "The design takes in retrieved or pasted content but does not fence "
                           "it before it reaches a prompt.")]
    return []


def oversight_deviations(profile: dict) -> list:
    from app.modules.enterprise_genome.services.ai_system_profile import (
        HIGH_AUTONOMY_LEVELS,
        UNKNOWN,
        risk_flags,
    )

    out = []
    gov = profile.get("governance") or {}
    autonomy = profile.get("autonomy_level")
    if autonomy == UNKNOWN:
        out.append(_deviation("oversight", "autonomy-not-recorded",
                              "The system's autonomy level is not recorded, so the oversight "
                              "requirement cannot be checked."))
    elif autonomy in HIGH_AUTONOMY_LEVELS and gov.get("approval_gate") is not True:
        out.append(_deviation("oversight", "approval-gate-missing",
                              f"The system runs at '{autonomy}' autonomy without a recorded "
                              "approval gate."))
    sensitivity = profile.get("data_sensitivity")
    if sensitivity == UNKNOWN:
        out.append(_deviation("oversight", "data-sensitivity-not-recorded",
                              "The system's data sensitivity is not recorded, so the human "
                              "review requirement cannot be checked."))
    elif sensitivity == "regulated" and gov.get("human_review") is not True:
        out.append(_deviation("oversight", "human-review-missing",
                              "The system handles regulated data without a recorded human review."))
    if profile.get("owner") is None:
        out.append(_deviation("oversight", "no-accountable-owner",
                              "The system has no recorded owner accountable for it."))
    if "retired-model" in risk_flags(profile):
        out.append(_deviation("oversight", "retired-model",
                              f"The system is pointed at a retired model "
                              f"({profile.get('model_id')})."))
    return out


def review_deviations(element, declaration: Optional[dict]) -> list:
    """Every deviation of this design from the pattern list, the fencing rule
    and the oversight requirements, in that order. Empty means none found."""
    from app.modules.enterprise_genome.services.ai_system_profile import profile_from_element

    declaration = declaration or {}
    return (
        pattern_deviations(declaration)
        + fencing_deviations(declaration)
        + oversight_deviations(profile_from_element(element))
    )


def get_review(decision_id, organization_id) -> Optional[ArchitectureDecision]:
    """One AI design review in *organization_id*, or ``None``."""
    if organization_id is None or decision_id is None:
        return None
    return ArchitectureDecision.query.filter(
        ArchitectureDecision.id == decision_id,
        ArchitectureDecision.organization_id == organization_id,
        ArchitectureDecision.ai_system_id.isnot(None),
    ).first()


def list_reviews(organization_id) -> list:
    if organization_id is None:
        return []
    return (
        ArchitectureDecision.query.filter(
            ArchitectureDecision.organization_id == organization_id,
            ArchitectureDecision.ai_system_id.isnot(None),
        )
        .order_by(ArchitectureDecision.id.desc())
        .all()
    )


def submit_design(
    *,
    element_id,
    organization_id,
    submitter_id,
    title,
    context,
    declaration,
) -> ArchitectureDecision:
    """Submit a design for the board: an ``ArchitectureDecision`` under review,
    linked to the AI system. One open submission per system at a time."""
    if organization_id is None:
        raise DesignReviewError("No organisation is set, so no design can be submitted.")
    element = scoped_ai_system(element_id, organization_id)
    if element is None:
        raise DesignReviewError("AI system not found.")
    title = (title or "").strip()
    if not title:
        raise DesignReviewError("A design submission needs a title.")
    if len(title) > 200:
        raise DesignReviewError("The title is limited to 200 characters.")
    pattern = (declaration.get("pattern") or "").strip()
    if len(pattern) > 80:
        raise DesignReviewError("The pattern name is limited to 80 characters.")
    clean = {
        "pattern": pattern or None,
        "ingests_untrusted_content": declaration.get("ingests_untrusted_content"),
        "untrusted_input_fenced": declaration.get("untrusted_input_fenced"),
    }
    for key in ("ingests_untrusted_content", "untrusted_input_fenced"):
        if clean[key] not in (True, False, None):
            raise DesignReviewError("Fencing answers must be yes, no or left unanswered.")

    from app.modules.enterprise_genome.services.ai_system_classification import (
        _lock_element,
    )

    if _lock_element(element_id, organization_id) is None:
        raise DesignReviewError("AI system not found.")
    open_row = ArchitectureDecision.query.filter(
        ArchitectureDecision.organization_id == organization_id,
        ArchitectureDecision.ai_system_id == element_id,
        ArchitectureDecision.status.in_(OPEN_STATUSES),
    ).first()
    if open_row is not None:
        raise DesignReviewError(
            f"A design for this system is already under review ({open_row.decision_id}); "
            "the board decides it before another is submitted."
        )
    row = ArchitectureDecision(
        organization_id=organization_id,
        decision_id=ArchitectureDecision.next_decision_id(),
        title=title,
        status=STATUS_SUBMITTED,
        context=(context or "").strip() or None,
        decision_type="platform",
        authority_level="enterprise_arb",
        enterprise_level=True,
        created_by_id=submitter_id,
        ai_system_id=element_id,
        archimate_element_ids=[element_id],
        ai_design_review={"declaration": clean},
    )
    db.session.add(row)
    db.session.flush()
    return row


def record_decision(
    *,
    decision_id,
    organization_id,
    decider_id,
    outcome,
    rationale,
) -> ArchitectureDecision:
    """Record the board's decision. Refused when: the review is not in this
    organisation, it was already decided (a decided review is never decided
    again), the decider submitted it, the rationale is missing where one is
    required (a rejection, or an acceptance with deviations). The deviations
    listed at the moment of decision are kept on the row."""
    if organization_id is None:
        raise DesignReviewError("No organisation is set, so no decision can be recorded.")
    if outcome not in (STATUS_ACCEPTED, STATUS_REJECTED):
        raise DesignReviewError("The decision must be accept or reject.")
    review = get_review(decision_id, organization_id)
    if review is None:
        raise DesignReviewError("Design review not found.")
    if review.status not in OPEN_STATUSES:
        raise DesignReviewError(
            f"This review was already decided ({review.status}); a decided review "
            "is not decided again."
        )
    if decider_id is None or review.created_by_id == decider_id:
        raise DesignReviewError(
            "You submitted this design, so you cannot also decide it. A separate "
            "board member must decide it."
        )
    element = scoped_ai_system(review.ai_system_id, organization_id)
    if element is None:
        raise DesignReviewError("The AI system under review no longer exists in this organisation.")
    deviations = review_deviations(element, (review.ai_design_review or {}).get("declaration"))
    rationale = (rationale or "").strip()
    if not rationale and (outcome == STATUS_REJECTED or deviations):
        raise DesignReviewError(
            "A rationale is required for a rejection, and for accepting a design "
            "that has listed deviations."
        )

    now = datetime.utcnow()
    snapshot = dict(review.ai_design_review or {})
    snapshot["deviations_at_decision"] = deviations
    values = {
        "status": outcome,
        "decided_by_id": decider_id,
        "decided_at": now,
        "ai_design_review": snapshot,
    }
    if outcome == STATUS_ACCEPTED:
        values.update(approved_by_id=decider_id, approved_at=now, rationale=rationale or None,
                      decision="Accepted")
    else:
        values.update(rejection_reason=rationale, decision="Rejected")
    # Claim the open row in one statement: of two deciders, the second finds
    # the status already moved and changes nothing.
    claimed = db.session.execute(
        update(ArchitectureDecision)
        .where(
            ArchitectureDecision.id == review.id,
            ArchitectureDecision.organization_id == organization_id,
            ArchitectureDecision.status.in_(OPEN_STATUSES),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.session.rollback()
        raise DesignReviewError("This review was decided by someone else just now.")
    db.session.expire(review)
    return review
