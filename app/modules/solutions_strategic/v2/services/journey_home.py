"""Read model for the Architecture Journey home.

One screen has to answer, for a piece of architecture work that may never become a
solution: what is this for, where has it got to, who is on it, what evidence stands
behind it, what has been decided, what is at risk, and what happens next.

Two rules govern everything below.

**A count that was not computed is ``None``, never ``0``.** The template renders
``None`` as an em dash. This is not a stylistic preference: on a governance screen a
"0" that means "the query failed" is indistinguishable from a "0" that means "there
are genuinely no open risks", and the reader acts on the difference. When the link
store cannot be read the view is returned with ``degraded=True`` and null counts, so
the screen can say plainly that it does not know.

**Nothing is copied from a system of record.** Titles and statuses are resolved from
the owning table at read time, or the item is shown as an unresolved reference. A
cached title is a fact that silently stops being true.
"""

from __future__ import annotations

import logging

from app import db
from app.models.architecture_journey import (
    ARCHITECTURE_LAYERS,
    JOURNEY_STAGES,
    ArchitectureJourney,
)
from app.models.architecture_journey_link import (
    JOURNEY_LINK_ENTITY_TYPES,
    ArchitectureJourneyLink,
    ArchitectureJourneyMember,
)
from app.models.ai_chat_document import AIChatDocumentUpload
from app.models.architecture_decision import ArchitectureDecision
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement, ArchitectureModel
from app.models.architecture_review_board import ARBReviewItem
from app.models.business_capabilities import BusinessCapability
from app.models.implementation_migration import WorkPackage
from app.models.risk import Risk
from app.models.solution_models import Solution
from app.models.strategic import StrategicInitiative
from app.models.transformation_decision import DecisionBrief
from app.models.unified_capability import ValueStream


logger = logging.getLogger(__name__)


def _resolver(model, *, label_attr, status_attr=None):
    """Build a tenant-explicit target resolver for one closed link type."""
    def resolve(entity_id, organization_id):
        row = db.session.execute(
            db.select(model).where(
                model.id == entity_id,
                model.organization_id == organization_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        status = getattr(row, status_attr, None) if status_attr else None
        if hasattr(status, "value"):
            status = status.value
        return {
            "entity_id": row.id,
            "label": getattr(row, label_attr, None),
            "status": status,
        }
    return resolve


# Closed by construction and asserted against JOURNEY_LINK_ENTITY_TYPES in tests.
JOURNEY_LINK_RESOLVERS = {
    # Repointed to the register ArchitectureDecision ARB already uses (R1-02,
    # security.md 5.4). Existing links that still name an
    # ArchitectureDecisionRecord id are remapped by
    # `flask repoint-journey-decision-links` before this change ships;
    # see app/commands/repoint_journey_decision_links.py.
    "decision": _resolver(ArchitectureDecision, label_attr="title", status_attr="status"),
    "decision_brief": _resolver(DecisionBrief, label_attr="title", status_attr="status"),
    "risk": _resolver(Risk, label_attr="title", status_attr="status"),
    "document": _resolver(AIChatDocumentUpload, label_attr="original_filename", status_attr="status"),
    "archimate_element": _resolver(ArchiMateElement, label_attr="name", status_attr="status"),
    "architecture_model": _resolver(ArchitectureModel, label_attr="name"),
    "work_package": _resolver(WorkPackage, label_attr="name", status_attr="status"),
    "capability": _resolver(BusinessCapability, label_attr="name"),
    "value_stream": _resolver(ValueStream, label_attr="name"),
    "application": _resolver(ApplicationComponent, label_attr="name", status_attr="lifecycle_status"),
    "arb_review": _resolver(ARBReviewItem, label_attr="title", status_attr="status"),
    "programme": _resolver(StrategicInitiative, label_attr="name", status_attr="status"),
    "solution": _resolver(Solution, label_attr="name", status_attr="status"),
}

assert set(JOURNEY_LINK_RESOLVERS) == set(JOURNEY_LINK_ENTITY_TYPES)


def resolve_journey_link_target(entity_type, entity_id, organization_id):
    """Resolve an allowed target without revealing foreign/missing distinctions."""
    resolver = JOURNEY_LINK_RESOLVERS.get(entity_type)
    return resolver(entity_id, organization_id) if resolver else None


INTENT_LABELS = {
    "business_transformation": "Business transformation",
    "operating_model": "Operating-model change",
    "strategy_to_execution": "Strategy to execution",
    "portfolio_change": "Portfolio change",
    "risk_and_compliance": "Regulatory and risk response",
    "architecture_assessment": "Architecture assessment",
    "solution_design": "Solution design",
}

STAGE_LABELS = {
    "frame": "Frame the problem",
    "discover": "Discover the current state",
    "shape": "Shape the options",
    "decide": "Decide and record",
    "deliver": "Deliver and govern",
}

# What the journey should do next, and *why*. The reason matters: an instruction
# with no reason is a demand, and the architect cannot tell whether it applies to
# their situation.
STAGE_NEXT_ACTION = {
    "frame": (
        "State the purpose and who is affected",
        "A journey without a stated purpose cannot be reviewed, and its outputs "
        "cannot be traced back to a reason for doing the work.",
    ),
    "discover": (
        "Attach the evidence you already have",
        "Discovery is finished when the decision can be argued from records that "
        "exist, rather than from recollection.",
    ),
    "shape": (
        "Record the options considered",
        "An option that was rejected is part of the decision. Without it, a future "
        "reader cannot tell what was weighed.",
    ),
    "decide": (
        "Record the decision, or record that no change is needed",
        "\"No change\" is a legitimate architectural outcome and deserves the same "
        "traceability as a change.",
    ),
    "deliver": (
        "Confirm governance and hand over",
        "Delivery without a governance record leaves the work unaccounted for at "
        "the next review.",
    ),
}

# Which link types roll up into which headline count on the home screen.
_COUNT_BUCKETS = {
    "decisions": ("decision", "decision_brief"),
    "risks": ("risk",),
    "documents": ("document",),
    "architecture": ("archimate_element", "architecture_model", "capability", "value_stream"),
    "delivery": ("work_package", "programme", "solution"),
    "governance": ("arb_review",),
}


def _load_links(journey_id, organization_id):
    """Every link on this journey.

    Split out so a test can force it to fail: the degraded path is the one that
    matters most on this screen and it must be exercisable.
    """
    return (
        db.session.execute(
            db.select(ArchitectureJourneyLink).where(
                ArchitectureJourneyLink.journey_id == journey_id,
                ArchitectureJourneyLink.organization_id == organization_id,
            )
        )
        .scalars()
        .all()
    )


def _load_members(journey_id, organization_id):
    return (
        db.session.execute(
            db.select(ArchitectureJourneyMember).where(
                ArchitectureJourneyMember.journey_id == journey_id,
                ArchitectureJourneyMember.organization_id == organization_id,
            )
        )
        .scalars()
        .all()
    )


def _resolve_journey(journey_id, organization_id):
    """Explicit (id, organization_id) predicate.

    Deliberately not ``Session.get()``: per AGENTS.md it is tenant-scoped only on an
    identity-map miss, and on a hit returns the cached row with no SQL and therefore
    no tenant filter at all.
    """
    return db.session.execute(
        db.select(ArchitectureJourney).where(
            ArchitectureJourney.id == journey_id,
            ArchitectureJourney.organization_id == organization_id,
        )
    ).scalar_one_or_none()


def _stage_block(journey):
    stage = journey.current_stage or "frame"
    try:
        index = JOURNEY_STAGES.index(stage)
    except ValueError:
        # An unknown stage is a data problem, not a reason to guess a position on
        # the progress bar. Report it as unknown rather than silently showing 0/5.
        logger.warning("journey %s has unrecognised stage %r", journey.id, stage)
        index = None
    return {
        "key": stage,
        "label": STAGE_LABELS.get(stage, stage.replace("_", " ").title()),
        "index": index,
        "total": len(JOURNEY_STAGES),
        "stages": [
            {"key": key, "label": STAGE_LABELS.get(key, key.title())} for key in JOURNEY_STAGES
        ],
    }


def _next_action(journey):
    label, reason = STAGE_NEXT_ACTION.get(
        journey.current_stage or "frame",
        (
            "Review the journey",
            "This journey is at a stage with no defined next step; check its state.",
        ),
    )
    return {"label": label, "reason": reason}


def journey_home_view(*, journey_id, actor_user):
    """The whole home screen, or ``None`` if this actor may not see this journey.

    ``None`` covers both "no such journey" and "belongs to another organisation",
    deliberately: distinguishing them would let a caller probe which ids exist in
    another tenant.
    """
    organization_id = getattr(actor_user, "organization_id", None)
    if not organization_id:
        return None

    journey = _resolve_journey(journey_id, organization_id)
    if journey is None:
        return None

    is_admin = bool(actor_user.is_admin()) or bool(
        getattr(actor_user, "is_platform_admin", False)
    )
    if journey.owner_id != actor_user.id and not is_admin:
        membership = db.session.execute(
            db.select(ArchitectureJourneyMember.id).where(
                ArchitectureJourneyMember.journey_id == journey.id,
                ArchitectureJourneyMember.user_id == actor_user.id,
                ArchitectureJourneyMember.organization_id == organization_id,
            )
        ).scalar_one_or_none()
        if membership is None:
            return None

    intent = journey.intent or ""
    view = {
        "journey": journey,
        "can_manage_members": journey.owner_id == actor_user.id or is_admin,
        "purpose": {
            "intent": intent,
            "label": INTENT_LABELS.get(intent, intent.replace("_", " ").title() or None),
            "title": journey.title,
            "outcome_type": journey.outcome_type,
        },
        "stage": _stage_block(journey),
        "next_action": _next_action(journey),
        "layers": [
            {"key": key, "selected": key in (journey.selected_layers or [])}
            for key in ARCHITECTURE_LAYERS
        ],
        "degraded": False,
        "links": {},
        "participants": [],
        "counts": {},
    }

    try:
        links = _load_links(journey.id, organization_id)
        members = _load_members(journey.id, organization_id)
    except Exception:
        # The screen still renders -- purpose, stage and next action come from the
        # journey row itself and are known. Everything sourced from links is
        # reported as unknown rather than as zero.
        logger.exception("journey %s: link/member load failed", journey.id)
        view["degraded"] = True
        view["counts"] = {bucket: None for bucket in _COUNT_BUCKETS}
        view["counts"]["participants"] = None
        view["participants"] = []
        view["links"] = {}
        return view

    by_type = {}
    for link in links:
        resolved = resolve_journey_link_target(
            link.entity_type, link.entity_id, organization_id
        )
        if resolved is None:
            logger.warning(
                "journey %s: ignoring unresolved %s:%s link %s",
                journey.id, link.entity_type, link.entity_id, link.id,
            )
            if link.entity_type != "decision":
                # Unchanged for every other link kind (out of R1-02's scope;
                # sdd.md 9.2 is decision-only).
                continue
            # US-15 AC2: a decision link whose target is gone is shown, not
            # silently dropped, so a deleted decision does not read as "this
            # journey never had one".
            resolved = {
                "entity_id": link.entity_id,
                "label": f"Record no longer available (link {link.id})",
                "status": None,
                "unresolved": True,
            }
        resolved.update({
            "id": link.id,
            "entity_type": link.entity_type,
            "relation": link.relation,
            "note": link.note,
        })
        by_type.setdefault(link.entity_type, []).append(resolved)
    view["links"] = by_type

    view["counts"] = {
        bucket: sum(len(by_type.get(entity_type, [])) for entity_type in entity_types)
        for bucket, entity_types in _COUNT_BUCKETS.items()
    }

    # The owner is a participant whether or not anyone added a membership row --
    # the journey cannot exist without one, so counting only explicit rows would
    # under-report by one on every journey.
    from app.models.user import User
    user_ids = {journey.owner_id, *(member.user_id for member in members)}
    users = db.session.execute(
        db.select(User).where(
            User.id.in_(user_ids), User.organization_id == organization_id
        )
    ).scalars().all()
    users_by_id = {user.id: user for user in users}
    participants = []
    for member in members:
        user = users_by_id.get(member.user_id)
        if user is None:
            continue
        participants.append({
            "user_id": member.user_id,
            "role": member.role,
            "is_owner": member.user_id == journey.owner_id,
            "name": " ".join(filter(None, (user.first_name, user.last_name))) or user.email,
            "email": user.email,
        })
    if not any(participant["is_owner"] for participant in participants):
        participants.insert(
            0, {
                "user_id": journey.owner_id,
                "role": "owner",
                "is_owner": True,
                "name": (
                    " ".join(filter(None, (
                        users_by_id[journey.owner_id].first_name,
                        users_by_id[journey.owner_id].last_name,
                    ))) or users_by_id[journey.owner_id].email
                ),
                "email": users_by_id[journey.owner_id].email,
            }
        )
    view["participants"] = participants
    view["counts"]["participants"] = len(participants)

    return view


__all__ = [
    "INTENT_LABELS",
    "JOURNEY_LINK_RESOLVERS",
    "STAGE_LABELS",
    "journey_home_view",
    "resolve_journey_link_target",
]
