"""The proposal store for onboarding's Review screen (onboarding-redesign-v3
§6, "Review (P2 Auto)").

A proposal is a hypothesis about one field of the organisation's profile --
industry, company size, region, funding stage, tech stack, compliance
hints, customer segment -- carrying a source and a confidence, produced by
one of the functions in ``producers.py``. Confirming (or editing then
confirming) a proposal writes the value into the organisation's profile
through ``profile.write()``, the one existing writer of
``Organization.settings["onboarding"]``; dismissing writes nothing there --
it only records that this suggestion should not be offered again. Nothing
reaches the profile without a person's own action.

Reuse note (searched before writing this): app/models and
app/modules/ai_chat hold two existing "confirm or reject a suggested
change" stores --
``AIChatCRUDApproval`` (app/models/ai_chat_crud_approval.py, table
``ai_chat_crud_approvals``) and ``ApprovalWorkflow``
(app/models/capability_governance.py, table ``approval_workflow``). Neither
fits: both approve *execution of an entity CRUD operation* -- create,
update, delete a capability, application or vendor, or a multi-step
governance decision workflow -- through a fixed operation/entity-type
dispatch table (see app/modules/ai_chat/approval_gate.py and
AIChatApprovalService.approve_and_execute); approving one *runs* a write
against those specific tables. A proposal here is not an entity CRUD
operation at all -- it is a candidate value for one field of the
organisation's *onboarding profile* (already a plain JSON document, not a
row-per-entity table), carrying a page-or-answer source and a confidence
word neither existing model has a column for, and "approving" it must call
the existing, single ``profile.write()`` path rather than a dispatch table
of entity writers. Building a fifth approval-shaped SQL table to hold that
would itself be the duplication the reuse rule warns against; extending
either existing table with organisation-profile-field semantics it was not
designed for would be worse. Proposals therefore live beside the rest of
onboarding's per-organisation state, in
``Organization.settings["onboarding_proposals"]`` -- the exact JSON-under-
settings shape ``profile.py`` already established (see its own module
docstring: "the one reader and writer of Organization.settings['onboarding']"),
just a second, sibling key on the same column, owned by this one module.

A later, larger piece of work already has a name for the fuller version of
this concept in the reuse register (docs/artifacts/reuse-register.yml in
the orchestrator repository, concept id ``company-context-intake``): one
``company_context_runs`` row per website-reading run, holding step-zero
proposals and their decisions, alongside the register's separate
``approval-queue`` concept (``ai_chat_crud_approvals``) for canvas
candidates specifically. Neither exists in this codebase yet -- the run
record, the fetcher-driven producer and the canvas intake client are a
separate, not-yet-built piece of work (see producers.py's docstring) -- so
there is nothing there yet to point this module's from-answers proposals
at. Building ``company_context_runs`` now, ahead of its own briefed shape
(organisation, source address, pages fetched, model-call accounting and
more, per the register's own description), would be guessing at another
role's design rather than reusing it. This module's store is the honest
stopgap for the one producer this brief asks for; when the run record
lands, the natural move is for it to read and decide through that table
instead of Organization.settings, and this docstring's reuse note travels
with it as the reason recorded for the tech-lead or solution-architect to
accept or correct.

A proposal's id is deterministic -- ``"<field>:<slug of its value>"`` -- so
re-running a producer with an unchanged signal lands on the same stored
decision instead of creating a duplicate, and a dismissed proposal is not
re-offered next time the Review screen loads.
"""
from __future__ import annotations

import datetime
import re

from app import db
from app.models.organization import Organization

from . import profile

_KEY = "onboarding_proposals"

FIELD_LABELS = {
    "industry": "Industry",
    "company_size": "Size",
    "region": "Region",
    "funding_stage": "Funding stage",
    "tech_stack": "Tech stack",
    "compliance_hints": "Compliance hints",
    "customer_segment": "Customer segment",
}

# Fields where confirming a proposal adds to what is recorded rather than
# replacing it (a company can have more than one compliance hint or tech).
MULTI_VALUE_FIELDS = {"tech_stack", "compliance_hints"}

_ACTIONS = ("confirm", "edit", "dismiss")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "value"


def proposal_id(field: str, value: str) -> str:
    """The deterministic id for a (field, value) candidate."""
    return f"{field}:{_slug(value)}"


def _store(org: Organization) -> dict:
    """A copy of *org*'s proposal store, safe to mutate.

    Both the top-level dict and each stored proposal dict are copied here --
    not just the top level -- because ``decide()`` mutates a proposal dict
    in place before ``_save()`` reassigns ``org.settings`` as a whole. A
    shallow copy alone would leave the returned proposal dicts as the same
    objects still referenced from ``org.settings``: mutating them would
    silently update the "current" value SQLAlchemy compares against too, so
    the reassignment in ``_save()`` would compare equal to its own baseline
    and the column's change would never be flushed. This was caught by
    test_dismiss_never_reappears_as_pending and the isolation tests during
    this build -- see the build report.
    """
    settings = org.settings or {}
    return {pid: dict(proposal) for pid, proposal in settings.get(_KEY, {}).items()}


def _save(org: Organization, store: dict) -> None:
    settings = dict(org.settings or {})
    settings[_KEY] = store
    org.settings = settings
    db.session.add(org)
    db.session.commit()


def read(org: Organization) -> list[dict]:
    """Every stored proposal for *org*, newest first."""
    store = _store(org)
    return sorted(store.values(), key=lambda p: p.get("created_at") or "", reverse=True)


def sync(org: Organization, candidates: list[dict]) -> list[dict]:
    """Merge freshly-produced *candidates* into *org*'s proposal store.

    A candidate already decided (confirmed, edited or dismissed) keeps its
    decision -- a producer running again never reopens a person's choice.
    A candidate still pending is refreshed in place (its reason or
    confidence wording may have changed). A stored *pending* proposal whose
    id no longer appears among *candidates* (the signal that produced it
    changed or went away) is dropped; a decided one is always kept, as the
    record of what was confirmed or dismissed.
    """
    store = _store(org)
    now = datetime.datetime.utcnow().isoformat()
    seen_ids = set()
    for candidate in candidates:
        pid = proposal_id(candidate["field"], candidate["value"])
        seen_ids.add(pid)
        existing = store.get(pid)
        if existing and existing.get("status") != "pending":
            continue  # a decision stands; the producer does not reopen it
        store[pid] = {
            "id": pid,
            "field": candidate["field"],
            "field_label": FIELD_LABELS.get(candidate["field"], candidate["field"]),
            "value": candidate["value"],
            "source": candidate["source"],
            "source_label": candidate["source_label"],
            "confidence": candidate["confidence"],
            "reason": candidate.get("reason"),
            "status": "pending",
            "decided_value": None,
            "created_at": (existing or {}).get("created_at", now),
            "decided_at": None,
            "decided_by_user_id": None,
        }
    for pid in list(store):
        if store[pid]["status"] == "pending" and pid not in seen_ids:
            del store[pid]
    _save(org, store)
    return read(org)


def decide(
    org: Organization,
    the_proposal_id: str,
    action: str,
    *,
    value: str | None = None,
    user_id: int | None = None,
) -> dict:
    """Confirm, edit-then-confirm, or dismiss one stored proposal.

    Confirming or editing writes the decided value into the organisation's
    profile through ``profile.write()`` and nothing else. Dismissing writes
    nothing to the profile -- it only marks the proposal so it is not
    offered again.

    Raises ``KeyError`` for an unknown proposal id and ``ValueError`` for an
    unknown action or an empty edited value.
    """
    if action not in _ACTIONS:
        raise ValueError("invalid_action")

    store = _store(org)
    proposal = store.get(the_proposal_id)
    if proposal is None:
        raise KeyError(the_proposal_id)

    if action == "dismiss":
        proposal["status"] = "dismissed"
        proposal["decided_value"] = None
    else:
        final_value = proposal["value"] if action == "confirm" else (value or "").strip()
        if not final_value:
            raise ValueError("empty_value")
        proposal["status"] = "confirmed" if action == "confirm" else "edited"
        proposal["decided_value"] = final_value
        _write_to_profile(org, proposal["field"], final_value)

    proposal["decided_at"] = datetime.datetime.utcnow().isoformat()
    proposal["decided_by_user_id"] = user_id
    store[the_proposal_id] = proposal
    _save(org, store)
    return proposal


def _write_to_profile(org: Organization, field: str, value: str) -> None:
    if field in MULTI_VALUE_FIELDS:
        current = list(profile.read(org).get(field) or [])
        if value not in current:
            current.append(value)
        profile.write(org, **{field: current})
    else:
        profile.write(org, **{field: value})
