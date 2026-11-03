"""Decision governance: where the model has drifted from its architecture decisions.

Every check here reads the organisation's own model and decisions and nothing
else -- no code scan, no LLM -- so the same model always gives the same
findings. Each finding says what is wrong, why it matters, what to do next, and
who it goes to: the decision's owner, or the stakeholder whose concern is open.

Checks (one key each, used by the page and the tests):

``no_owner``             a live decision nobody owns
``owner_not_consulted``  an affected element's owner is not on the decision
``missing_element``      a decision points at an element no longer in the model
``unanswered_concern``   a stakeholder's driver no live decision addresses
``expired_exception``    a deviation whose valid-until date has passed
``nothing_affected``     an accepted decision names no element, so it cannot be checked
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app import db
from app.models.architecture_decision import (
    PARTY_ELEMENT_TYPES,
    ArchitectureDecision,
    DecisionParty,
    normalise_element_type,
)
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

# Decisions still in force or on their way to it. Rejected, superseded,
# deprecated and expired ones no longer govern anything.
LIVE_STATUSES = ("proposed", "under_review", "accepted", "approved")
IN_FORCE = ("accepted", "approved")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

_OWNERSHIP = ("assignment",)
_CONCERN_LINKS = ("association", "influence")


@dataclass
class Finding:
    key: str
    severity: str
    title: str
    why: str
    next: str
    decision_id: int | None = None
    decision_label: str | None = None
    element_id: int | None = None
    element_name: str | None = None
    route_to: list[str] = field(default_factory=list)


def _label(decision):
    return f"{decision.decision_id}: {decision.title}" if decision.decision_id else decision.title


def _ids(raw):
    out = []
    for value in raw or ():
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def findings(org_id: int, now: datetime | None = None) -> list[Finding]:
    """All governance findings for one organisation, most severe first."""
    now = now or datetime.utcnow()
    decisions = db.session.execute(
        db.select(ArchitectureDecision).where(
            ArchitectureDecision.organization_id == org_id,
            ArchitectureDecision.status.in_(LIVE_STATUSES),
        )
    ).scalars().all()

    parties = db.session.execute(
        db.select(DecisionParty).where(DecisionParty.organization_id == org_id)
    ).scalars().all()
    by_decision: dict[int, list[DecisionParty]] = {}
    for p in parties:
        by_decision.setdefault(p.decision_id, []).append(p)

    referenced = {i for d in decisions for i in _ids(d.archimate_element_ids)}
    elements = {
        e.id: e for e in db.session.execute(
            db.select(ArchiMateElement).where(ArchiMateElement.organization_id == org_id)
        ).scalars().all()
    }
    party_ids = {i for i, e in elements.items() if normalise_element_type(e.type) in PARTY_ELEMENT_TYPES}

    relationships = db.session.execute(
        db.select(ArchiMateRelationship).where(ArchiMateRelationship.organization_id == org_id)
    ).scalars().all()
    owners_of: dict[int, set[int]] = {}
    concerns: dict[int, set[int]] = {}  # driver id -> stakeholder ids
    for r in relationships:
        kind = (r.type or "").lower()
        if kind in _OWNERSHIP and r.source_id in party_ids and r.target_id in elements:
            owners_of.setdefault(r.target_id, set()).add(r.source_id)
        if kind in _CONCERN_LINKS:
            for stakeholder, driver in ((r.source_id, r.target_id), (r.target_id, r.source_id)):
                s, d = elements.get(stakeholder), elements.get(driver)
                if s and d and normalise_element_type(s.type) == "stakeholder" \
                        and normalise_element_type(d.type) == "driver":
                    concerns.setdefault(driver, set()).add(stakeholder)

    out: list[Finding] = []
    for d in decisions:
        mine = by_decision.get(d.id, [])
        owner_names = sorted({p.element.name for p in mine if p.role == "owner" and p.element})
        on_decision = {p.element_id for p in mine if p.role in ("decider", "owner", "consulted")}
        in_force = (d.status or "") in IN_FORCE
        route = owner_names or ["No owner yet: the architecture team"]

        if not owner_names:
            out.append(Finding(
                "no_owner", "high" if in_force else "medium", "Nobody owns this decision",
                "Without an owner, drift against this decision is found but never acted on.",
                "Add the Stakeholder, Business Actor or Business Role who answers for it as owner.",
                d.id, _label(d), route_to=route))

        affected = _ids(d.archimate_element_ids)
        if in_force and not affected:
            out.append(Finding(
                "nothing_affected", "low", "This decision names no element it affects",
                "A decision tied to nothing in the model cannot be checked for drift.",
                "Edit the decision and pick the capabilities, processes, applications or nodes it governs.",
                d.id, _label(d), route_to=route))

        for element_id in affected:
            element = elements.get(element_id)
            if element is None:
                out.append(Finding(
                    "missing_element", "medium", "The decision points at an element no longer in the model",
                    f"Element {element_id} was removed, so this decision governs something that is gone.",
                    "Re-point the decision at what replaced it, or supersede the decision.",
                    d.id, _label(d), element_id, None, route))
                continue
            for owner_id in sorted(owners_of.get(element_id, ())):
                if owner_id in on_decision:
                    continue
                owner = elements[owner_id]
                out.append(Finding(
                    "owner_not_consulted", "medium", f"{owner.name} owns {element.name} but is not on the decision",
                    f"{owner.name} is assigned to {element.name}, which this decision affects, "
                    "and was not recorded as decider, owner or consulted.",
                    f"Consult {owner.name} and add them to the decision, or record why they were not.",
                    d.id, _label(d), element.id, element.name, route + [owner.name]))

        if (d.decision_type or "") == "deviation" and d.valid_until and d.valid_until < now and in_force:
            out.append(Finding(
                "expired_exception", "high", "This exception has passed its end date",
                f"The deviation was granted until {d.valid_until:%d %b %Y} and is still treated as in force.",
                "Close the deviation by fixing what it excused, or record a new decision extending it.",
                d.id, _label(d), route_to=route))

    for driver_id, stakeholder_ids in sorted(concerns.items()):
        if driver_id in referenced:
            continue
        driver = elements[driver_id]
        names = sorted(elements[s].name for s in stakeholder_ids)
        out.append(Finding(
            "unanswered_concern", "low", f"No decision addresses {driver.name}",
            f"{', '.join(names)} hold{'s' if len(names) == 1 else ''} this concern, "
            "and no live decision names it.",
            "Record a decision that answers it, or link an existing decision to this driver.",
            None, None, driver.id, driver.name, names))

    out.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.key, f.decision_label or "", f.element_name or ""))
    return out
