"""R1-B56: Agent Registry service.

Registers an agent with an owner, a charter version (R1-B22's AgentCharter)
and delegated limits; refuses activation until all three are set. A charter
change is a diff against the approved version, routed through R1-B07's one
approval queue -- never a second review screen -- and a clause that removes
one of the shared non-removable rules is refused at write time, not just
flagged in review.

TB-0496 (owner-leaver pause + successor recommendation) is explicitly out of
scope here: it depends on a leaver signal that does not exist in this
codebase yet (R1-B26's identity-provider directory-sync provisioning is
unmerged). The
model carries the two columns a follow-up PR needs; nothing here sets them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.models.agent_charter import AgentCharter
from app.models.agent_registration import AgentRegistration

# The shared rules every charter must keep (scripts/check_ai_evidence_rules.py
# enforces the same list on the static human-persona charters). A charter
# version diff that drops one of these phrases from forbidden_actions, or
# adds it to proposable_actions, is refused -- not silently accepted and not
# merely flagged for a reviewer to miss.
NON_REMOVABLE_RULES = [
    "no_fabrication",
    "cite_source",
    "propose_dont_dispose",
    "governance_wins",
]


class CharterChangeRefused(ValueError):
    """A proposed charter version would weaken a non-removable rule."""


class CrossOrganisationOwner(ValueError):
    """The chosen owner does not belong to the registration's organisation.

    Security fix (6 Oct 2026 review): set_owner previously stored any
    owner_user_id from the form with no organisation check, and the detail
    page then rendered that user's email -- any administrator could type
    another organisation's user id and read their email, and enumerate ids
    to read every user's email across tenants.
    """


class InvalidDelegatedLimit(ValueError):
    """A delegated limit value is not a usable positive integer."""


def create_registration(
    *, organization_id: int, name: str, purpose: str = ""
) -> AgentRegistration:
    reg = AgentRegistration(
        organization_id=organization_id, name=name, purpose=purpose, status="draft",
    )
    db.session.add(reg)
    db.session.commit()
    return reg


def set_owner(registration: AgentRegistration, owner_user_id: int) -> None:
    """Refuses (CrossOrganisationOwner) rather than store an owner from
    another organisation -- see that exception's docstring."""
    from app.models.user import User

    owner = User.query.filter_by(
        id=owner_user_id, organization_id=registration.organization_id,
    ).first()
    if owner is None:
        raise CrossOrganisationOwner(
            "That user does not belong to this organisation."
        )
    registration.owner_user_id = owner_user_id
    db.session.commit()


def set_delegated_limits(registration: AgentRegistration, limits: Dict[str, Any]) -> None:
    """Refuses (InvalidDelegatedLimit) a non-positive max_writes_per_day --
    0 or negative is not a usable write budget, and silently accepting one
    would read as "activation is fine" while actually permitting nothing or
    something nonsensical."""
    max_writes = limits.get("max_writes_per_day")
    if max_writes is not None and (not isinstance(max_writes, int) or max_writes <= 0):
        raise InvalidDelegatedLimit("max_writes_per_day must be a positive integer.")
    registration.delegated_limits = limits
    db.session.commit()


def _violates_non_removable_rules(
    forbidden_actions: List[str], proposable_actions: List[str]
) -> List[str]:
    """Rules the proposed version would weaken, named -- empty when clean."""
    violations = []
    for rule in NON_REMOVABLE_RULES:
        if rule not in (forbidden_actions or []):
            violations.append(rule)
        if rule in (proposable_actions or []):
            violations.append(rule)
    return sorted(set(violations))


def assign_charter(
    registration: AgentRegistration,
    *,
    persona: str,
    purpose: str,
    readable_entities: List[str],
    proposable_actions: List[str],
    forbidden_actions: List[str],
    charter_text: str,
) -> AgentCharter:
    """Create the next charter version for this organisation's persona and
    point the registration at it. Refuses (raises CharterChangeRefused)
    rather than accept a version that drops a non-removable rule."""
    forbidden_actions = list(forbidden_actions or []) + [
        r for r in NON_REMOVABLE_RULES if r not in (forbidden_actions or [])
    ]
    violations = _violates_non_removable_rules(forbidden_actions, proposable_actions)
    if violations:
        raise CharterChangeRefused(
            "Charter version would weaken non-removable rule(s): " + ", ".join(violations)
        )

    current = AgentCharter.current_for(persona, registration.organization_id)
    next_version = (current.version + 1) if current else 1
    charter = AgentCharter(
        organization_id=registration.organization_id,
        persona=persona,
        version=next_version,
        purpose=purpose,
        readable_entities=readable_entities,
        proposable_actions=proposable_actions,
        forbidden_actions=forbidden_actions,
        charter_text=charter_text,
    )
    db.session.add(charter)
    db.session.flush()
    registration.charter_persona = persona
    registration.charter_version_id = charter.id
    db.session.commit()
    return charter


def _diff_summary(old: Optional[AgentCharter], proposed: Dict[str, Any]) -> str:
    new_forbidden = proposed.get("forbidden_actions") or []
    new_proposable = proposed.get("proposable_actions") or []
    if old is None:
        return f"New charter for {proposed['persona']}: first version."
    added_forbidden = sorted(set(new_forbidden) - set(old.forbidden_actions or []))
    removed_forbidden = sorted(set(old.forbidden_actions or []) - set(new_forbidden))
    added_proposable = sorted(set(new_proposable) - set(old.proposable_actions or []))
    removed_proposable = sorted(set(old.proposable_actions or []) - set(new_proposable))
    parts = [f"Charter change for {proposed['persona']}: v{old.version} -> v{old.version + 1}."]
    if added_forbidden:
        parts.append(f"Forbids: {', '.join(added_forbidden)}.")
    if removed_forbidden:
        parts.append(f"No longer forbids: {', '.join(removed_forbidden)}.")
    if added_proposable:
        parts.append(f"Can now propose: {', '.join(added_proposable)}.")
    if removed_proposable:
        parts.append(f"Can no longer propose: {', '.join(removed_proposable)}.")
    return " ".join(parts)


def request_charter_change(
    registration: AgentRegistration,
    *,
    persona: str,
    purpose: str,
    readable_entities: List[str],
    proposable_actions: List[str],
    forbidden_actions: List[str],
    charter_text: str,
    requested_by_user_id: Optional[int],
):
    """Route a proposed charter version through R1-B07's approval queue --
    the version is NOT created yet, so AgentCharter.current_for never sees
    an unreviewed change as current. Refuses up front (before anything is
    queued) when the proposal would weaken a non-removable rule; the actual
    AgentCharter row is created only when the approval is approved, by
    execute_charter_change below (dispatched from
    ai_chat_approval_service.approve_and_execute)."""
    forbidden_actions = list(forbidden_actions or []) + [
        r for r in NON_REMOVABLE_RULES if r not in (forbidden_actions or [])
    ]
    violations = _violates_non_removable_rules(forbidden_actions, proposable_actions)
    if violations:
        raise CharterChangeRefused(
            "Charter version would weaken non-removable rule(s): " + ", ".join(violations)
        )

    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    old = AgentCharter.current_for(persona, registration.organization_id)
    proposed = {
        "persona": persona,
        "purpose": purpose,
        "readable_entities": readable_entities,
        "proposable_actions": proposable_actions,
        "forbidden_actions": forbidden_actions,
        "charter_text": charter_text,
    }
    summary = _diff_summary(old, proposed)
    approval = create_approval_record(
        organization_id=registration.organization_id,
        operation_type="agent_charter_change",
        entity_type="agent_registration",
        entity_id=registration.id,
        summary=summary,
        operation_payload=proposed,
        user_id=requested_by_user_id,
        original_command=f"system: charter change for {persona}",
    )
    db.session.commit()
    return approval


def execute_charter_change(registration: AgentRegistration, proposed: Dict[str, Any]) -> AgentCharter:
    """Create the approved charter version and point the registration at
    it. Called only from the approval dispatcher, after a second reviewer
    has approved -- never from the request path above."""
    return assign_charter(
        registration,
        persona=proposed["persona"],
        purpose=proposed.get("purpose", ""),
        readable_entities=proposed.get("readable_entities") or [],
        proposable_actions=proposed.get("proposable_actions") or [],
        forbidden_actions=proposed.get("forbidden_actions") or [],
        charter_text=proposed.get("charter_text", ""),
    )


def activate(registration: AgentRegistration) -> Tuple[bool, List[str]]:
    ok, missing = registration.activate()
    db.session.commit()
    return ok, missing


def list_registrations(organization_id: int):
    return (
        AgentRegistration.query.filter_by(organization_id=organization_id)
        .order_by(AgentRegistration.created_at.desc())
        .all()
    )


def get_registration(organization_id: int, registration_id: int) -> Optional[AgentRegistration]:
    return AgentRegistration.query.filter_by(
        id=registration_id, organization_id=organization_id,
    ).first()
