"""People (or teams) and what they do, captured at onboarding, written to the real tables.

Nothing here is an onboarding copy:
  * a person or team is a ``BusinessActor`` (``Individual`` / ``Team``); creating one
    already creates its ArchiMate element;
  * "who does which capability" is an ``EnterpriseRaciAssignment`` written through
    ``organization.service.upsert_raci_cell``, the same call the RACI matrix uses;
  * how well they do it is one ``CapabilityProficiency`` row per RACI cell, the only
    new store, because nothing else could hold it.

How people are captured depends on company size. A small company names each
person. A large one rates teams (with a headcount) and can still name the few key
individuals. Onboarding never deletes an actor; it only clears an assignment the
founder explicitly removed.

Proficiency is personal data about a named individual. It is written only when the
founder gives it, and NULL means "not assessed".
"""
from __future__ import annotations

import datetime

from flask import g

from app import db
from app.models.business_layer import BusinessActor
from app.models.organization_model import (
    PROFICIENCY_LEVELS,
    RACI_VALUES,
    CapabilityProficiency,
    EnterpriseRaciAssignment,
)
from app.models.unified_capability import UnifiedCapability
from app.modules.organization import service as raci_service

from . import capabilities as capability_capture

_KINDS = {"person": "Individual", "team": "Team"}
_MAX_NAME = 255
_MAX_TEAM = 1_000_000

PROFICIENCY = (
    {"level": 1, "name": "Learning", "meaning": "Can do it with help."},
    {"level": 2, "name": "Working", "meaning": "Does it on their own for routine cases."},
    {"level": 3, "name": "Proficient", "meaning": "Handles the hard cases too."},
    {"level": 4, "name": "Leads", "meaning": "Others come to them; they could teach it."},
)

ROLES = (
    {"raci": "R", "name": "Does the work"},
    {"raci": "A", "name": "Answers for it"},
    {"raci": "C", "name": "Advises"},
)

# Default way of capturing people at each size. "person" = name people one by one,
# "team" = rate teams with a headcount. Either can be added at any size.
_DEFAULT_KIND = {"micro": "person", "small": "person", "mid": "team", "large": "team"}


def default_kind(size_band: str) -> str:
    return _DEFAULT_KIND.get(size_band, "person")


def _unified_by_label() -> dict[str, UnifiedCapability]:
    """The organisation's projected capability rows, keyed by lower-cased name."""
    rows = UnifiedCapability.query.filter_by(source_table="business_capability").all()
    return {(r.name or "").strip().lower(): r for r in rows}


def _clean_name(value) -> str | None:
    text = (str(value) if value is not None else "").strip()
    return text[:_MAX_NAME] or None


def _clean_headcount(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= _MAX_TEAM else None


def _clean_level(value) -> int | None:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if level in PROFICIENCY_LEVELS else None


def read(stage: str, size_band: str) -> dict:
    """Existing people/teams with their capability assignments, plus the offered choices.

    ``capabilities`` lists only capabilities already recorded for the organisation,
    since a person can only be attached to a capability that exists."""
    unified = _unified_by_label()
    recorded = [
        {"key": c["key"], "label": c["label"], "unified_id": unified[c["label"].lower()].id}
        for c in capability_capture.catalogue_for(stage, size_band)
        if c["label"].lower() in unified
    ]
    by_unified = {c["unified_id"]: c["key"] for c in recorded}

    actors = BusinessActor.query.filter(BusinessActor.actor_type.in_(list(_KINDS.values()))).order_by(BusinessActor.id).all()
    cells = EnterpriseRaciAssignment.query.filter_by(stakeholder_type="actor").all()
    levels = {p.raci_assignment_id: p.level for p in CapabilityProficiency.query.all()}
    by_actor: dict[int, list[dict]] = {}
    for cell in cells:
        key = by_unified.get(cell.capability_id)
        if key:
            by_actor.setdefault(cell.stakeholder_id, []).append(
                {"key": key, "role": cell.raci, "proficiency": levels.get(cell.id)}
            )
    people = [
        {
            "id": a.id,
            "name": a.name,
            "kind": "team" if a.actor_type == "Team" else "person",
            "headcount": a.headcount if a.actor_type == "Team" and a.headcount else None,
            "assignments": by_actor.get(a.id, []),
        }
        for a in actors
    ]
    return {
        "people": people,
        "capabilities": [{"key": c["key"], "label": c["label"]} for c in recorded],
        "default_kind": default_kind(size_band),
    }


def _upsert_actor(name: str, kind: str, headcount: int | None, actor_id, org_id: int) -> BusinessActor:
    actor_type = _KINDS[kind]
    actor = None
    if actor_id:
        actor = db.session.get(BusinessActor, actor_id)
        # Belongs to this organisation and is a person or team: this stops the route
        # from renaming another tenant's actor or one of this tenant's departments.
        if actor is not None and (actor.organization_id != org_id or actor.actor_type not in _KINDS.values()):
            actor = None
    if actor is None:
        # Scoped by organisation explicitly: outside a request the tenant listener
        # does not filter, and a name match must never cross organisations.
        actor = BusinessActor.query.filter_by(organization_id=org_id, name=name, actor_type=actor_type).first()
    if actor is None:
        actor = BusinessActor(name=name, actor_type=actor_type, organization_id=org_id)
        db.session.add(actor)
    actor.name = name
    actor.actor_type = actor_type
    actor.headcount = 1 if kind == "person" else headcount
    db.session.flush()
    return actor


def _set_proficiency(cell: EnterpriseRaciAssignment, level: int | None) -> None:
    row = CapabilityProficiency.query.filter_by(raci_assignment_id=cell.id).first()
    if level is None:
        if row is not None:
            db.session.delete(row)
        return
    if row is None:
        row = CapabilityProficiency(raci_assignment_id=cell.id)
        db.session.add(row)
    row.level = level
    row.assessed_at = datetime.datetime.utcnow()
    row.source = "onboarding"


def save(entries: list[dict], *, stage: str, size_band: str, org_id: int | None = None) -> dict:
    """Create or update people/teams and their capability assignments.

    Each entry is ``{"id"?, "name", "kind": "person"|"team", "headcount"?,
    "assignments": [{"key", "role": "R"|"A"|"C"|null, "proficiency": 1-4|null}]}``.
    A ``role`` of null clears that assignment. Unknown capability keys, capabilities
    not yet recorded, and unnamed entries are ignored.
    Returns ``{"people": n, "assignments": n}``.
    """
    org_id = org_id or getattr(g, "current_org_id", None)
    unified = _unified_by_label()
    key_to_unified = {
        c["key"]: unified[c["label"].lower()]
        for c in capability_capture.catalogue_for(stage, size_band)
        if c["label"].lower() in unified
    }
    people = assignments = 0
    for entry in entries or []:
        name = _clean_name((entry or {}).get("name"))
        if not name:
            continue
        kind = entry.get("kind") if entry.get("kind") in _KINDS else "person"
        actor = _upsert_actor(name, kind, _clean_headcount(entry.get("headcount")), entry.get("id"), org_id)
        people += 1
        for item in entry.get("assignments") or []:
            capability = key_to_unified.get((item or {}).get("key"))
            if capability is None:
                continue
            role = item.get("role")
            if role is None or role == "":
                raci_service.delete_raci_cell("actor", actor.id, capability.id)
                continue
            if role not in RACI_VALUES:
                continue
            cell = raci_service.upsert_raci_cell("actor", actor.id, actor.name, capability.id, role)
            _set_proficiency(cell, _clean_level(item.get("proficiency")))
            assignments += 1
    db.session.commit()
    return {"people": people, "assignments": assignments}


def record_named(org, named: list[dict]) -> dict:
    """Make the "Who's on the team" answers real people.

    Tell us more collects ``{"name", "does"}`` rows. Each becomes an Individual
    business actor for the organisation (the same rows the People step edits), with
    ``does`` kept as its description when it has none yet. No capability is
    assumed from a free-text role; attaching one is the People step's job."""
    created = 0
    for row in named or []:
        name = _clean_name((row or {}).get("name"))
        if not name:
            continue
        existing = BusinessActor.query.filter_by(organization_id=org.id, name=name, actor_type="Individual").first()
        actor = _upsert_actor(name, "person", None, existing.id if existing else None, org.id)
        created += existing is None
        does = (str((row or {}).get("does") or "")).strip()
        if does and not actor.description:
            actor.description = does[:500]
    return {"people_created": created}
